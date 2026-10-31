"""Tests d'intégration sandbox réelle.

Marqueur `@pytest.mark.smoke` : exclus du run pytest par défaut. Exécution
explicite : `pytest -m smoke tests/smoke/`.

Pré-requis : PENNYLANE_API_TOKEN configuré (env ou .env racine repo).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from datetime import datetime
from decimal import Decimal

import pytest

from grandlivre import (
    LedgerEntriesResource,
    PennylaneClient,
    Settings,
    SupplierInvoicesResource,
    TrialBalanceResource,
)

# Marqueur appliqué à tout le module.
pytestmark = pytest.mark.smoke

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
_IDENTIFIANT = re.compile(r"\b\d{7,}\b")


def _sans_identifiants_reels(obj: object) -> object:
    """Le même JSON, chaque nombre de 7 chiffres et plus (identifiant de l'éditeur dans un nombre ou une
    URL, numéro de compte auxiliaire du dossier) remplacé par une valeur fictive courte et stable
    (1001, 1002…), même valeur → même remplaçant. Les montants, dates et numéros de compte du plan
    général (6 chiffres au plus) ne sont pas touchés."""
    texte = json.dumps(obj, ensure_ascii=False, default=str)
    table: dict[str, str] = {}
    for reel in sorted(set(_IDENTIFIANT.findall(texte)), key=lambda s: (len(s), s)):
        table[reel] = str(1001 + len(table))
    return json.loads(_IDENTIFIANT.sub(lambda m: table[m.group(0)], texte))


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


# Le settings sandbox réutilise les env vars existantes (cabinet placeholder).
def _real_settings() -> Settings:
    return Settings()


async def test_smoke_fiscal_years_available() -> None:
    """Récupère la liste des exercices. Au moins un doit exister."""
    async with PennylaneClient(settings=_real_settings()) as client:
        response = await client.request("GET", "/fiscal_years")
    items = response.get("items", [])
    assert len(items) > 0, "Aucun exercice fiscal exposé par le dossier de test."
    # Vérifie la forme de base d'un exercice.
    for fy in items:
        assert "id" in fy
        assert "start" in fy
        assert "finish" in fy


async def test_smoke_trial_balance_first_exercice() -> None:
    """Appelle TrialBalanceResource sur le premier exercice trouvé."""
    async with PennylaneClient(settings=_real_settings()) as client:
        fy_response = await client.request("GET", "/fiscal_years")
        items = fy_response.get("items", [])
        assert items, "Pré-requis : au moins un exercice."
        # Prendre l'année du premier exercice.
        first_start = items[0]["start"]  # ex "2024-01-01"
        exercice = first_start.split("-")[0]

        tb = await TrialBalanceResource(client).get(exercice=exercice)
    assert tb.exercice == exercice
    # On accepte 0 compte (dossier vide) mais le contrat structurel doit tenir.
    for account in tb.accounts:
        assert isinstance(account.debits, Decimal)
        assert isinstance(account.credits, Decimal)
        assert isinstance(account.balance, Decimal)
        # balance = debits - credits.
        assert account.balance == account.debits - account.credits


async def test_smoke_ledger_entries_first_exercice() -> None:
    """LedgerEntriesResource sans filter account_number, cap=10."""
    async with PennylaneClient(settings=_real_settings()) as client:
        fy_response = await client.request("GET", "/fiscal_years")
        items = fy_response.get("items", [])
        assert items
        exercice = items[0]["start"].split("-")[0]

        page = await LedgerEntriesResource(client).paginate(exercice=exercice, cap=10)
    assert page.exercice_resolved["start"]
    assert page.count <= 10
    for line in page.lines:
        assert isinstance(line.debit, Decimal)
        assert isinstance(line.credit, Decimal)


async def test_smoke_supplier_invoices_minimal() -> None:
    """SupplierInvoicesResource avec cap=10. Tolère un dossier vide."""
    async with PennylaneClient(settings=_real_settings()) as client:
        page = await SupplierInvoicesResource(client).paginate(cap=10)
    assert page.count <= 10
    # Pas d'assertion forte sur le contenu : un dossier de test peut être vide.


async def test_smoke_capture_ledger_schema() -> None:
    """Capture le JSON brut d'une réponse /ledger_entry_lines pour fixture.

    Résout la dette P1 module A.2 (schéma /ledger_entry_lines
    empirique vérifié).
    """
    FIXTURES_DIR.mkdir(exist_ok=True)
    async with PennylaneClient(settings=_real_settings()) as client:
        fy_response = await client.request("GET", "/fiscal_years")
        items = fy_response.get("items", [])
        assert items, "Pré-requis : au moins un exercice."
        start = items[0]["start"]
        finish = items[0]["finish"]

        filter_clauses = [
            {"field": "date", "operator": "gteq", "value": start},
            {"field": "date", "operator": "lteq", "value": finish},
        ]
        response = await client.request(
            "GET",
            "/ledger_entry_lines",
            params={"filter": json.dumps(filter_clauses), "limit": 5},
        )

    today = datetime.now().strftime("%Y-%m-%d")
    fixture_path = FIXTURES_DIR / f"ledger_entry_lines_sample_{today}.json"

    # Sanitisation du cursor de pagination : le cursor Pennylane est du JSON
    # base64-encoded (non-secret), mais sa forme déclenche detect-secrets
    # (entropie élevée). Remplacement par un placeholder car il n'est pas
    # rejouable hors session.
    response_for_fixture = {**response, "items": response.get("items", [])[:5]}
    if response_for_fixture.get("next_cursor"):
        response_for_fixture["next_cursor"] = "<OPAQUE-CURSOR-sanitized>"
    # Identifiants réels de l'éditeur (lignes, écritures, journaux, comptes : 7 chiffres et plus, dans
    # les nombres comme dans les URL) remplacés par des valeurs fictives courtes et stables : la fixture
    # est suivie dans la bibliothèque publique (revue du 30/10/2026), la forme du JSON est gardée.
    response_for_fixture = _sans_identifiants_reels(response_for_fixture)

    fixture_payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "endpoint": "/ledger_entry_lines",
        "filters": filter_clauses,
        "response_keys_at_top_level": sorted(response.keys()),
        "items_count": len(response.get("items", [])),
        "first_item_keys": (sorted(response["items"][0].keys()) if response.get("items") else []),
        "raw_response_truncated": response_for_fixture,
    }
    fixture_path.write_text(
        json.dumps(fixture_payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Assertions de structure attendue (selon module A.2).
    assert "items" in response
    items = response["items"]
    if items:
        first = items[0]
        # Champs canoniques attendus (schéma NEW API 2026, cf. tools.py:158-194).
        assert "id" in first
        assert "debit" in first or "credit" in first or "ledger_account" in first
