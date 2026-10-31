"""Modèles métier des lignes d'écritures comptables.

Schéma normalisé : chaque ligne expose l'identifiant et la date de son
écriture parente (`entry_id`, `entry_date`), son journal (`journal_id`), son
compte (`account_number`), ses montants `debit` / `credit` en Decimal et les
identifiants des lignes lettrées avec elle (`lettered_line_ids`, vide pour une
ligne non lettrée).
Les en-têtes d'écriture (libellé, numéro de facture, numéro de pièce,
échéance) ne sont pas portés par `/ledger_entry_lines` : voir
`LedgerEntriesGroupedResource` (`/ledger_entries`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ..parsing import parse_decimal


def _to_decimal(value: Any) -> Decimal:
    return parse_decimal(value)


PennyDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


class LedgerEntryLine(BaseModel):
    """Une ligne d'écriture (`/ledger_entry_lines`) — schéma normalisé."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    entry_id: int | None = None
    entry_date: str | None = None
    journal_id: int | None = None
    line_id: int
    line_label: str = ""
    account_number: str = ""
    debit: PennyDecimal
    credit: PennyDecimal
    #: Lignes du même lettrage (``lettered_ledger_entry_lines.ids`` de l'API) ; vide = non lettrée.
    lettered_line_ids: list[int] = Field(default_factory=list)


class LedgerSummary(BaseModel):
    """Totaux calculés côté client sur les lignes effectivement retournées."""

    total_debit: PennyDecimal
    total_credit: PennyDecimal
    balance: PennyDecimal


class LedgerEntriesPage(BaseModel):
    """Page de lignes d'écritures avec cap dur et contexte de continuation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    lines: list[LedgerEntryLine]
    truncated: bool
    next_cursor: str | None = None
    exercice_resolved: dict[str, Any]
    filters_applied: dict[str, Any]
    count: int
    summary: LedgerSummary | None = None
    summary_unavailable_reason: str | None = None
