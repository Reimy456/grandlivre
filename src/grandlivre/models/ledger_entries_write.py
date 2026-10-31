"""Modèles du module write — création d'écritures (`POST /ledger_entries`).

ADR (module write WAL outbox). Contient les modèles d'ENTRÉE (`NewLedgerEntry`
/ `NewLedgerEntryLine`, primitives) ET le modèle de RÉPONSE 201 (`CreatedLedgerEntry`
/ `CreatedLedgerEntryLine`) — ce dernier porte les `id` de lignes serveur nécessaires
au read-back de réconciliation (§2.4) et à la liaison `compte→ledger_account_id` (§2.10).

Schéma `POST /api/external/v2/ledger_entries` (R-code-1, `openapi_spec_raw.json`) :

    requis      : date, label, journal_id, ledger_entry_lines[]
    optionnels  : due_date, currency, piece_number, ledger_attachment_id,
                  file_attachment_id
    ligne requis    : debit, credit, ledger_account_id
    ligne optionnel : label

Tous les champs de la spec sont modélisés → `extra="forbid"` : sur un write, une
clé inconnue est une faute de frappe (silencieusement ignorée par l'API sinon),
refusée à la construction.

Discipline montants : `debit` / `credit` sont des `Decimal` (jamais `float`),
sérialisés en chaîne en mode JSON pour le payload Pennylane. L'équilibre
Σ débits = Σ crédits (quantize ROUND_HALF_UP) est vérifié par
`LedgerEntriesResource.create()` AVANT le POST (`UnbalancedEntryError`),
cf. ADR — pas ici : le modèle porte la *forme*, `create()` porte la
*garde* d'équilibre. Le modèle accepte donc une écriture structurellement valide
mais déséquilibrée ; c'est `create()` qui la refuse.
"""

from __future__ import annotations

import re
from datetime import date as _date
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    field_serializer,
    field_validator,
    model_validator,
)

from ..parsing import parse_decimal

_ZERO = Decimal("0")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_decimal(value: Any) -> Decimal:
    return parse_decimal(value)


PennyDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


def _validate_iso_date(value: str, *, field: str) -> str:
    """YYYY-MM-DD strict + date réelle (jour/mois valides). Refuse les datetime."""
    if not _DATE_RE.match(value):
        raise ValueError(f"{field} doit être au format YYYY-MM-DD : {value!r}")
    try:
        _date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} : date invalide (jour/mois hors plage) : {value!r}") from exc
    return value


class NewLedgerEntryLine(BaseModel):
    """Ligne d'une écriture à créer (`ledger_entry_lines[]`).

    Invariant double-partie : exactement un de `debit` / `credit` est strictement
    positif, l'autre nul (XOR), les deux non négatifs. Les deux champs sont
    toujours émis dans le payload (requis par l'API, l'un à « 0 »). Une ligne
    portant deux montants non nuls relève de deux lignes — refusée.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    ledger_account_id: int
    debit: PennyDecimal = _ZERO
    credit: PennyDecimal = _ZERO
    label: str | None = None

    @field_validator("debit", "credit")
    @classmethod
    def _non_negative(cls, v: Decimal) -> Decimal:
        if v < _ZERO:
            raise ValueError("debit/credit doit être >= 0 (pas de montant signé)")
        return v

    @model_validator(mode="after")
    def _exactly_one_side(self) -> NewLedgerEntryLine:
        if (self.debit > _ZERO) == (self.credit > _ZERO):
            raise ValueError(
                "ligne invalide : exactement un de debit/credit doit être > 0 "
                f"(debit={self.debit}, credit={self.credit})"
            )
        return self

    @field_serializer("debit", "credit", when_used="json")
    def _amount_to_str(self, value: Decimal) -> str:
        return str(value)


class NewLedgerEntry(BaseModel):
    """Écriture comptable à créer (`POST /ledger_entries`).

    Tous les champs de la spec sont modélisés explicitement → `extra="forbid"`.
    L'équilibre des lignes est vérifié par `create()` (ADR), pas ici.
    Les optionnels valant `None` sont exclus du payload par `create()`
    (`model_dump(mode="json", exclude_none=True)`).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    date: str
    label: str
    journal_id: int
    ledger_entry_lines: list[NewLedgerEntryLine]
    # Optionnels (spec POST), exclus du payload si None.
    due_date: str | None = None
    currency: str | None = None
    piece_number: str | None = None
    ledger_attachment_id: int | None = None
    file_attachment_id: int | None = None

    @field_validator("date")
    @classmethod
    def _date_iso(cls, v: str) -> str:
        return _validate_iso_date(v, field="date")

    @field_validator("due_date")
    @classmethod
    def _due_date_iso(cls, v: str | None) -> str | None:
        return None if v is None else _validate_iso_date(v, field="due_date")

    @field_validator("label")
    @classmethod
    def _label_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("label ne doit pas être vide")
        return v

    @field_validator("ledger_entry_lines")
    @classmethod
    def _at_least_two(cls, v: list[NewLedgerEntryLine]) -> list[NewLedgerEntryLine]:
        if len(v) < 2:
            raise ValueError("une écriture requiert >= 2 lignes (partie double)")
        return v


# ---------------------------------------------------------------------------
# Réponse 201 — écriture créée (ADR, retour de create())
# ---------------------------------------------------------------------------


class CreatedLedgerEntryLine(BaseModel):
    """Ligne d'écriture telle que retournée par le DÉTAIL `GET /ledger_entries/{id}`
    et le POST 201 (avec `id` serveur).

    Le compte est exposé **imbriqué** : `ledger_account.id` (+ `number`/`url`) ; le
    champ plat `ledger_account_id` est **déprécié et ABSENT du détail** (vérifié
    R-code-1 : spec `openapi_spec_raw.json` + GET sandbox read-only). `resolved_account_id`
    résout l'imbriqué d'abord, fallback le plat, `None` si aucun — JAMAIS un `0`
    silencieux (qui fausserait la signature du read-back §2.4 → faux 0-match → doublon,
    cf. revue adversariale RB-3). `extra="allow"` : résilience (`status`, etc.).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: int | None = None
    ledger_account_id: int | None = None  # plat : déprécié/absent sur le détail
    debit: PennyDecimal = _ZERO
    credit: PennyDecimal = _ZERO
    label: str | None = None
    ledger_account: dict[str, Any] | None = None

    @property
    def resolved_account_id(self) -> int | None:
        """`ledger_account.id` imbriqué (préféré — déprécation du plat) → fallback
        `ledger_account_id` plat → `None` si aucun (jamais `0` silencieux)."""
        nested = (self.ledger_account or {}).get("id")
        if nested is not None:
            return int(nested)
        return self.ledger_account_id


class CreatedLedgerEntry(BaseModel):
    """Écriture créée telle que retournée par `POST /ledger_entries` (201).

    `id` (en-tête) requis : identifiant Pennylane mémorisé par le WAL (transition
    CONFIRMED) + utilisé par la couche applicative pour la liaison. Les lignes
    portent leurs `id` serveur (read-back §2.4). `extra="allow"` : résilience aux
    variations par type de journal + Sunset 2026. Schéma R-code-1 depuis
    `openapi_spec_raw.json` (réponse 201).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: int
    label: str | None = None
    date: str | None = None
    journal_id: int | None = None
    journal: dict[str, Any] | None = None
    piece_number: str | None = None
    due_date: str | None = None
    invoice_number: str | None = None
    ledger_attachment_id: int | None = None
    ledger_attachment_filename: str | None = None
    categories: list[Any] | None = None
    attachment: Any = None
    ledger_entry_lines: list[CreatedLedgerEntryLine] = []
