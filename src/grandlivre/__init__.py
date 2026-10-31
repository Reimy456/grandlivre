"""grandlivre — bibliothèque Python pour l'API Pennylane V2.

            + plan harmonic-hatching-trinket.md.

Exemples d'usage : voir README.md.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Source UNIQUE de version : la métadonnée du paquet installé (pyproject.toml).
    # Évite le version skew (ADR / checklist publication §A).
    __version__ = _pkg_version("grandlivre")
except PackageNotFoundError:  # checkout éditable / paquet non installé
    __version__ = "0.0.0+unknown"

from .client import PennylaneClient
from .config import Settings, get_settings
from .exceptions import (
    AccountNumberNotFoundError,
    FECExportFailedError,
    FiscalYearNotFoundError,
    PennylaneAuthError,
    PennylaneClientError,
    PennylaneError,
    PennylaneRateLimitError,
    PennylaneServerError,
    PennylaneValidationError,
    UnbalancedEntryError,
)
from .fiscal_years import FiscalYearInfo, resolve_fiscal_year
from .models import (
    SUPPLIER_PAYMENT_STATUSES,
    AnalyticalGeneralLedgerExportJob,
    AnalyticalGeneralLedgerExportRequest,
    BankAccount,
    ChangelogEntry,
    CreatedLedgerEntry,
    CreatedLedgerEntryLine,
    CreditNoteLinkAck,
    Customer,
    CustomerInvoice,
    CustomerInvoiceImportAck,
    CustomerInvoicesPage,
    FECExportJob,
    FECExportRequest,
    FECExportStatus,
    FiscalYear,
    GeneralLedgerExportJob,
    GeneralLedgerExportRequest,
    ImputationDates,
    InvoiceKind,
    InvoiceWriteAck,
    Journal,
    LedgerAccount,
    LedgerEntriesPage,
    LedgerEntryGrouped,
    LedgerEntryLine,
    LedgerEntryRef,
    LedgerSummary,
    LetteredLedgerEntryLine,
    MatchedInvoice,
    MeCompany,
    MeInfo,
    MeUser,
    NewLedgerEntry,
    NewLedgerEntryLine,
    Supplier,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierInvoicesPage,
    SupplierPaymentStatus,
    Transaction,
    TrialBalanceAccount,
    TrialBalanceResponse,
)
from .resources import (
    AnalyticalGeneralLedgerExportResource,
    BankAccountsResource,
    ChangelogsResource,
    CustomerInvoicesResource,
    CustomersResource,
    FECExportResource,
    FiscalYearsResource,
    GeneralLedgerExportResource,
    InvoicePaymentsResource,
    JournalsResource,
    LedgerAccountsResource,
    LedgerEntriesGroupedResource,
    LedgerEntriesResource,
    LetteringResource,
    MeResource,
    SupplierInvoicesResource,
    SuppliersResource,
    TransactionsResource,
    TrialBalanceResource,
)
from .wal import (
    DeploymentUnsafeError,
    IdempotencyStore,
    JsonlStore,
    NullStore,
    ReserveOutcome,
    ReserveResult,
    SqliteIdempotencyStore,
    WALDuplicatePayloadError,
    WALEntry,
    WALInDoubtError,
    WALState,
    WALStoreError,
    WALStoreRequiredError,
    ensure_deployment_safety,
)

__all__ = [
    "__version__",
    # Client + config
    "PennylaneClient",
    "Settings",
    "get_settings",
    # Resources
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
    # v0.3 P2 resources
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
    # Fiscal years (utilitaire public — v0.2 A.2)
    "FiscalYearInfo",
    "resolve_fiscal_year",
    # Models
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
    # v0.3 P2 models
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
    # Exceptions
    "PennylaneError",
    "PennylaneAuthError",
    "PennylaneRateLimitError",
    "PennylaneServerError",
    "PennylaneClientError",
    "PennylaneValidationError",
    "FiscalYearNotFoundError",
    "AccountNumberNotFoundError",
    "FECExportFailedError",
    "UnbalancedEntryError",
    "WALDuplicatePayloadError",
    "WALInDoubtError",
    "WALStoreError",
    "WALStoreRequiredError",
    # WAL idempotency store (anti-doublon write, ADR) — seam injectable
    # public : un store cross-host (p. ex. Postgres app-side) implémente ``IdempotencyStore``.
    "IdempotencyStore",
    "SqliteIdempotencyStore",
    "JsonlStore",
    "NullStore",
    "WALEntry",
    "WALState",
    "ReserveResult",
    "ReserveOutcome",
    "ensure_deployment_safety",
    "DeploymentUnsafeError",
]
