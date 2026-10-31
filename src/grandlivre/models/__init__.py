"""Modèles Pydantic v2 métier (wrappers au-dessus de _generated/).

Référence : plan harmonic-hatching-trinket.md Phase 4.
"""

from __future__ import annotations

from .bank_accounts import BankAccount
from .changelogs import ChangelogEntry
from .customer_invoices import (
    CreditNoteLinkAck,
    CustomerInvoice,
    CustomerInvoiceImportAck,
    CustomerInvoicesPage,
)
from .customers import Customer
from .exports import (
    AnalyticalGeneralLedgerExportJob,
    AnalyticalGeneralLedgerExportRequest,
    FECExportJob,
    FECExportRequest,
    FECExportStatus,
    GeneralLedgerExportJob,
    GeneralLedgerExportRequest,
)
from .fiscal_years import FiscalYear
from .invoice_writes import (
    SUPPLIER_PAYMENT_STATUSES,
    InvoiceKind,
    InvoiceWriteAck,
    SupplierPaymentStatus,
)
from .journals import Journal
from .ledger_accounts import LedgerAccount
from .ledger_entries import LedgerEntriesPage, LedgerEntryLine, LedgerSummary
from .ledger_entries_grouped import LedgerEntryGrouped
from .ledger_entries_write import (
    CreatedLedgerEntry,
    CreatedLedgerEntryLine,
    NewLedgerEntry,
    NewLedgerEntryLine,
)
from .lettering import LetteredLedgerEntryLine
from .me import MeCompany, MeInfo, MeUser
from .supplier_invoices import (
    ImputationDates,
    LedgerEntryRef,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierInvoicesPage,
)
from .suppliers import Supplier
from .transactions import MatchedInvoice, Transaction
from .trial_balance import TrialBalanceAccount, TrialBalanceResponse

__all__ = [
    "TrialBalanceAccount",
    "TrialBalanceResponse",
    "LedgerEntryLine",
    "LedgerEntriesPage",
    "LedgerSummary",
    "ImputationDates",
    "LedgerEntryRef",
    "SupplierInvoice",
    "SupplierInvoiceLine",
    "SupplierInvoicesPage",
    "MeInfo",
    "MeUser",
    "MeCompany",
    "FiscalYear",
    "Journal",
    "LedgerAccount",
    "LedgerEntryGrouped",
    "NewLedgerEntry",
    "NewLedgerEntryLine",
    "CreatedLedgerEntry",
    "CreatedLedgerEntryLine",
    "Transaction",
    "MatchedInvoice",
    "ChangelogEntry",
    # v0.3 P2
    "BankAccount",
    "Customer",
    "CustomerInvoice",
    "CustomerInvoiceImportAck",
    "CreditNoteLinkAck",
    "CustomerInvoicesPage",
    "FECExportJob",
    "FECExportRequest",
    "FECExportStatus",
    "GeneralLedgerExportJob",
    "GeneralLedgerExportRequest",
    "AnalyticalGeneralLedgerExportJob",
    "AnalyticalGeneralLedgerExportRequest",
    "LetteredLedgerEntryLine",
    "Supplier",
    # Module write factures (B-K9 partie 2)
    "InvoiceWriteAck",
    "InvoiceKind",
    "SupplierPaymentStatus",
    "SUPPLIER_PAYMENT_STATUSES",
]
