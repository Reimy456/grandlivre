"""Tests d'intégration sandbox étendue (Mission durcissement #2).

Extension `test_smoke_sandbox.py` pour 8 ressources « À vérifier » + 3 HTTP
403 résolution corollaire scope token sandbox read + write 6 mois cofondateur
Pennylane reçu 2026-05-18 ~19:00.

Cohérent ROADMAP §4.13.3 Mission durcissement #2 smoke sandbox étendu +
audit Mission 2 §3 tableau matrice empirique 18 ressources
(`.claude/audits/2026-05-18_audit_empirique_grandlivre_v0_3.md`
lignes 145-172).

Marqueur `@pytest.mark.smoke` : exclus du run pytest par défaut. Exécution
explicite : `pytest -m smoke tests/smoke/test_smoke_sandbox_extended.py`.

Pré-requis : PENNYLANE_API_TOKEN configuré (env ou .env racine repo) avec
scope étendu read + write cofondateur Pennylane.

Matrice 18 ressources audit Mission 2 (commit c71ce3d) :

- 5/18 OK confirmés (cf. test_smoke_sandbox.py existant) : TrialBalance +
  LedgerEntries + SupplierInvoices + Me + FiscalYears.
- 8/18 « À vérifier » empirique (cette extension) : Journals +
  LedgerAccounts + LedgerEntriesGrouped + CustomerInvoices + Customers +
  Lettering + Suppliers + Changelogs.
- 3/18 HTTP 403 résolution corollaire scope étendu (cette extension) :
  Transactions + BankAccounts + FECExport.

Pattern uniform `client.request("GET", "/endpoint")` cohérent existant
`test_smoke_sandbox.py:60` pour smoke minimal sans dépendance signatures
Resource précises.
"""

from __future__ import annotations

import os

import pytest

from grandlivre import (
    LedgerEntriesGroupedResource,
    LetteringResource,
    PennylaneClient,
    Settings,
)


def _has_token() -> bool:
    # 1. Variable d'environnement directe.
    token = os.getenv("PENNYLANE_API_TOKEN", "").strip()
    if token and token != "<your-pennylane-api-token>":
        return True
    # 2. Lecture .env (racine du projet).
    s = Settings()
    raw = s.pennylane_api_token.get_secret_value().strip()
    return bool(raw) and raw != "<your-pennylane-api-token>"


pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(
        not _has_token(),
        reason="PENNYLANE_API_TOKEN absent ou placeholder — smoke tests skipped.",
    ),
]


def _real_settings() -> Settings:
    """Settings sandbox réutilise les env vars existantes."""
    return Settings()


# ============================================================
# Catégorie 1 — 8 tests ressources « À vérifier » empirique
# Source : audit Mission 2 §3 tableau lignes 156-168 + cahier §4.2
# ============================================================


async def test_smoke_journals_list() -> None:
    """JournalsResource list endpoint /journals — audit §3 #6 « À vérifier »."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/journals")
    items = response.get("items", [])
    assert isinstance(items, list)


async def test_smoke_ledger_accounts_list() -> None:
    """LedgerAccountsResource list endpoint /ledger_accounts — audit §3 #7 « À vérifier »."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/ledger_accounts")
    items = response.get("items", [])
    assert isinstance(items, list)


async def test_smoke_ledger_entries_grouped_first_exercice() -> None:
    """LedgerEntriesGroupedResource endpoint /ledger_entries — audit §3 #8 « À vérifier ».

    Utilise Resource direct (vs `client.request` brut) pour éviter chain
    `/fiscal_years` + `/ledger_entries` qui déclenche transient 403 rate limit.
    """
    async with PennylaneClient(settings=_real_settings()) as client:
        entries, _truncated, _next_cursor = await LedgerEntriesGroupedResource(client).paginate(
            cap=5
        )
    assert isinstance(entries, list)


async def test_smoke_customer_invoices_minimal() -> None:
    """CustomerInvoicesResource endpoint /customer_invoices — audit §3 #12 « À vérifier »."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/customer_invoices", params={"limit": 5})
    items = response.get("items", [])
    assert isinstance(items, list)


async def test_smoke_customers_list() -> None:
    """CustomersResource endpoint /customers — audit §3 #13 « À vérifier »."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/customers", params={"limit": 5})
    items = response.get("items", [])
    assert isinstance(items, list)


async def test_smoke_lettering_parametrized_sequenced() -> None:
    """LetteringResource endpoint paramétré pattern séquencé — Mission #2 Phase 2+ partial.

    Pattern séquencé (audit Cs B.1 §6.1) :
    1. GET /ledger_entry_lines?limit=1 → extraction premier `id`
    2. GET /ledger_entry_lines/{id}/lettered_ledger_entry_lines via
       `LetteringResource(client).get_lettered_lines(id)` paramétré

    Cf. `lettering.py:38` `_path(ledger_entry_line_id)` + `lettering.py:44-63`
    `get_lettered_lines(ledger_entry_line_id) -> list[LetteredLedgerEntryLine]`.

    Scope token actuel `ledger_entry_lines:readonly` validé empirique Cs B.1
    (§3 #2 LedgerEntriesResource OK PASSED).

    Cas bordure : si sandbox vide ou aucune ligne lettrée trouvée,
    `LetteringResource.get_lettered_lines()` retourne liste vide légitime.
    """
    async with PennylaneClient(settings=_real_settings()) as client:
        # Étape 1 — extraction premier ledger_entry_line_id (limit=1)
        response = await client.request("GET", "/ledger_entry_lines", params={"limit": 1})
        items = response.get("items", [])
        if not items:
            pytest.skip(
                "Sandbox vide : aucun ledger_entry_line — pattern séquencé "
                "Lettering impossible. Skip motivé empirique."
            )
        first_id = items[0]["id"]
        # Étape 2 — GET paramétré LetteringResource
        lettered = await LetteringResource(client).get_lettered_lines(first_id)
    # Liste typée LetteredLedgerEntryLine (vide légitime si aucun lettrage).
    assert isinstance(lettered, list)


async def test_smoke_suppliers_list() -> None:
    """SuppliersResource endpoint /suppliers — audit §3 #18 « À vérifier »."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/suppliers", params={"limit": 5})
    items = response.get("items", [])
    assert isinstance(items, list)


async def test_smoke_changelogs_ledger_entry_lines_sub_endpoint() -> None:
    """ChangelogsResource sub-endpoint /changelogs/ledger_entry_lines — audit §3 #10 « À vérifier ».

    Endpoint réel selon `changelogs.py:47` (3 sous-endpoints valides :
    /changelogs/{ledger_entry_lines, supplier_invoices, transactions}).
    """
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request(
            "GET", "/changelogs/ledger_entry_lines", params={"limit": 5}
        )
    items = response.get("items", [])
    assert isinstance(items, list)


# ============================================================
# Catégorie 2 — 3 tests HTTP 403 résolution corollaire scope token read+write
# Source : audit Mission 2 §3 tableau lignes 159, 161, 164 + §5.4 scopes bloqués
# ============================================================


async def test_smoke_transactions_list_scope_extended() -> None:
    """TransactionsResource endpoint /transactions — audit §3 #9 HTTP 403 résolution corollaire scope `transactions:readonly` étendu cofondateur."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/transactions", params={"limit": 5})
    items = response.get("items", [])
    assert isinstance(items, list)


async def test_smoke_bank_accounts_list_scope_extended() -> None:
    """BankAccountsResource endpoint /bank_accounts — audit §3 #11 HTTP 403 résolution corollaire scope `bank_accounts:readonly` étendu cofondateur."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/bank_accounts")
    items = response.get("items", [])
    assert isinstance(items, list)


@pytest.mark.skip(
    reason="FECExportResource endpoints paramétrés (POST /exports/fecs création + "
    "GET /exports/fecs/{id} par ID, cf. exports.py:23-24). Pas de GET list "
    "sans ID confirmé empirique spec OpenAPI cumul Mission B.1 2026-05-19 "
    "(/exports/fecs : POST only ; /exports/fecs/{id} : GET only). Variante "
    "(ii) POST création destructive write production hors scope sandbox + "
    "scope `ledger` write indéterminé empirique scope token actuel. Smoke "
    "test scope = vérification scope token `ledger` étendu via résolution "
    "corollaire — différé Phase 2+ ultérieure avec POST création job FEC "
    "sandbox isolé scope ledger étendu confirmé. Documenté audit doc résultats."
)
async def test_smoke_fec_exports_list_scope_extended() -> None:
    """FECExportResource — audit §3 #14 SKIPPED endpoint paramétré (POST création + GET par ID)."""
    pass


# ============================================================
# Catégorie 3 — 2 tests corollaire exports famille SKIPPED motivés Mission B.1
# (audit Mission B.1 matrice 4 endpoints paramétrés Mission #2 Phase 2+ partial)
# Source : spec OpenAPI cumul `openapi_spec_raw.json` empirique 2026-05-19
# ============================================================


@pytest.mark.skip(
    reason="GeneralLedgerExportResource endpoints paramétrés strict pattern POST "
    "création + GET par ID confirmé empirique spec OpenAPI cumul Mission B.1 "
    "2026-05-19 (/exports/general_ledgers : POST only ; "
    "/exports/general_ledgers/{id} : GET only, cf. exports.py:188-220). Pas "
    "de GET list sans ID. Corollaire FECExport pattern symétrique. Variante "
    "(ii) POST création destructive write production hors scope sandbox + "
    "scope `ledger` write indéterminé empirique scope token actuel. Différé "
    "Phase 2+ ultérieure avec POST création job sandbox isolé scope ledger "
    "étendu confirmé. Documenté audit doc Mission B.1."
)
async def test_smoke_general_ledger_exports_list_scope_extended() -> None:
    """GeneralLedgerExportResource — Mission B.1 audit §3 #15 SKIPPED corollaire FECExport (POST création + GET par ID)."""
    pass


@pytest.mark.skip(
    reason="AnalyticalGeneralLedgerExportResource endpoints paramétrés strict "
    "pattern POST création + GET par ID confirmé empirique spec OpenAPI "
    "cumul Mission B.1 2026-05-19 "
    "(/exports/analytical_general_ledgers : POST only ; "
    "/exports/analytical_general_ledgers/{id} : GET only, cf. "
    "exports.py:282-320). Pas de GET list sans ID. Corollaire FECExport "
    "pattern symétrique + 3 champs request body (period_start, period_end, "
    "mode). Variante (ii) POST création destructive write production hors "
    "scope sandbox + scope `ledger` write indéterminé empirique scope token "
    "actuel. Différé Phase 2+ ultérieure avec POST création job sandbox "
    "isolé scope ledger étendu confirmé. Documenté audit doc Mission B.1."
)
async def test_smoke_analytical_general_ledger_exports_list_scope_extended() -> None:
    """AnalyticalGeneralLedgerExportResource — Mission B.1 audit §3 #16 SKIPPED corollaire FECExport (POST création + GET par ID)."""
    pass
