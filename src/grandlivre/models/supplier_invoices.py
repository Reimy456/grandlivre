"""Modèles métier des factures fournisseurs.

Le schéma exact dépend de la spec OpenAPI Pennylane. On expose un modèle
permissif avec les champs canoniques connus + `extra="allow"` pour préserver
les champs non listés ici (résilience face aux évolutions de spec).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict

from ..parsing import parse_decimal


def _to_decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return parse_decimal(value)


PennyDecimalOpt = Annotated[Decimal | None, BeforeValidator(_to_decimal_or_none)]


class LedgerEntryRef(BaseModel):
    """Lien vers l'écriture comptable générée par la facture (`ledger_entry: {id}`,
    schéma `SupplierInvoices__Response`, spec raw 2026). Clé de la jointure
    ligne d'écriture (`LedgerEntryLine.entry_id`) ↔ facture (lot 0 B0.2)."""

    model_config = ConfigDict(extra="allow")

    id: int


class ImputationDates(BaseModel):
    """Période d'imputation d'une ligne de facture (`imputation_dates`, nullable) :
    signal SECONDAIRE de période (affiché, jamais déclencheur tant que Q-CCA-04
    est sans réponse — journal EC entrée 1)."""

    model_config = ConfigDict(extra="allow")

    start_date: str | None = None
    end_date: str | None = None


class SupplierInvoiceLine(BaseModel):
    """Ligne d'une facture fournisseur (`GET /supplier_invoices/{id}/invoice_lines`,
    paginé `items` + `next_cursor`). Champs sourcés spec raw 2026 : id, label,
    description, amount, currency_amount, currency_amount_before_tax, currency_tax,
    tax, vat_rate, imputation_dates, created_at, updated_at."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")

    id: int | str
    label: str | None = None
    description: str | None = None
    amount: PennyDecimalOpt = None
    currency_amount: PennyDecimalOpt = None
    currency_amount_before_tax: PennyDecimalOpt = None
    currency_tax: PennyDecimalOpt = None
    tax: PennyDecimalOpt = None
    vat_rate: str | None = None
    imputation_dates: ImputationDates | None = None
    created_at: str | None = None
    updated_at: str | None = None


class SupplierInvoice(BaseModel):
    """Une facture fournisseur (`/supplier_invoices`)."""

    # `extra="allow"` : on préserve les champs API non explicitement listés ici,
    # cohérent avec la doctrine no-debt (pas d'incomplétude silencieuse).
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow",
    )

    id: int | str
    invoice_number: str | None = None
    label: str | None = None
    date: str | None = None
    # Échéance : la spec la nomme `deadline` (SupplierInvoices__Response), pas `due_date`.
    deadline: str | None = None
    currency: str | None = None
    amount: PennyDecimalOpt = None
    currency_amount: PennyDecimalOpt = None
    tax_amount: PennyDecimalOpt = None
    status: str | None = None
    supplier: dict[str, Any] | None = None
    # Lien vers l'écriture générée (typé au lot 0 B0.2 ; arrivait via `extra`).
    ledger_entry: LedgerEntryRef | None = None


class SupplierInvoicesPage(BaseModel):
    """Une page de factures fournisseurs paginée."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    invoices: list[SupplierInvoice]
    truncated: bool = False
    next_cursor: str | None = None
    count: int
