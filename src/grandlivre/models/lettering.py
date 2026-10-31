"""Modèle métier de l'endpoint `/ledger_entry_lines/{id}/lettered_ledger_entry_lines` (v0.3 B.9).

Lecture seule du lettrage : retourne les lignes d'écritures lettrées
ensemble avec une ligne donnée (rapprochement comptable).

Schéma capturé depuis `openapi_spec_raw.json`
(GET `/api/external/v2/ledger_entry_lines/{ledger_entry_line_id}/lettered_ledger_entry_lines`) :

    {
      "id": int,
      "debit": str (Decimal),
      "credit": str (Decimal),
      "label": str,
      "categories": list,
      "ledger_account": dict,
      "journal": dict,
      "date": str (ISO 8601),
      "ledger_entry": dict,
      "lettered_ledger_entry_lines": dict,
      "created_at": str,
      "updated_at": str
    }

Modèle dédié (vs réutilisation `LedgerEntryLine` v0.1) car le schéma
diffère de `/ledger_entry_lines` normalisé (variante locale) (clés non
préfixées `entry_*`, présence `ledger_account` / `journal` /
`ledger_entry` en sous-objets, `lettered_ledger_entry_lines` imbriqué).

`extra="allow"` préserve les champs API non listés (compatibilité forward).

**Périmètre P2 v0.3 = LECTURE SEULE**. POST/DELETE
`/ledger_entry_lines/lettering` (création / suppression lettrage)
différés P3 — action écriture, nécessite scope token élargi.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict

from ..parsing import parse_decimal


def _to_decimal(value: Any) -> Decimal:
    return parse_decimal(value)


PennyDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


class LetteredLedgerEntryLine(BaseModel):
    """Une ligne d'écriture lettrée avec une autre (réponse `/lettered_ledger_entry_lines`).

    Schéma natif Pennylane V2 préservé tel quel (vs `LedgerEntryLine`
    normalisé (variante locale)). Les sous-objets `ledger_account`,
    `journal`, `ledger_entry`, `categories` sont conservés en `dict` /
    `list` sans typage interne pour rester forward-compatible.
    """

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)

    id: int
    debit: PennyDecimal | None = None
    credit: PennyDecimal | None = None
    label: str | None = None
    date: str | None = None
    categories: list[Any] | None = None
    ledger_account: dict[str, Any] | None = None
    journal: dict[str, Any] | None = None
    ledger_entry: dict[str, Any] | None = None
    lettered_ledger_entry_lines: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
