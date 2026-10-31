"""Resources : façades pythoniques au-dessus du client.

Référence : plan harmonic-hatching-trinket.md Phase 4.
"""

from __future__ import annotations

from .bank_accounts import BankAccountsResource
from .changelogs import ChangelogsResource
from .customer_invoices import CustomerInvoicesResource
from .customers import CustomersResource
from .exports import (
    AnalyticalGeneralLedgerExportResource,
    FECExportResource,
    GeneralLedgerExportResource,
)
from .fiscal_years import FiscalYearsResource
from .invoice_payments import InvoicePaymentsResource
from .journals import JournalsResource
from .ledger_accounts import LedgerAccountsResource
from .ledger_entries import LedgerEntriesResource
from .ledger_entries_grouped import LedgerEntriesGroupedResource
from .lettering import LetteringResource
from .me import MeResource
from .supplier_invoices import SupplierInvoicesResource
from .suppliers import SuppliersResource
from .transactions import TransactionsResource
from .trial_balance import TrialBalanceResource

__all__ = [
    "TrialBalanceResource",
    "LedgerEntriesResource",
    "SupplierInvoicesResource",
    "MeResource",
    "FiscalYearsResource",
    "JournalsResource",
    "LedgerAccountsResource",
    "LedgerEntriesGroupedResource",
    "TransactionsResource",
    "ChangelogsResource",
    # v0.3 P2
    "BankAccountsResource",
    "CustomerInvoicesResource",
    "CustomersResource",
    "FECExportResource",
    "GeneralLedgerExportResource",
    "AnalyticalGeneralLedgerExportResource",
    "LetteringResource",
    "SuppliersResource",
    # Module write factures (B-K9 partie 2)
    "InvoicePaymentsResource",
]
