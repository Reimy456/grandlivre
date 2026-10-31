"""Sentinelle OpenAPI spec drift Pennylane V2 — Mode 1 baseline tracked.

Cs B.5 Mission durcissement #3 audit sécurité — Option C script Python
custom dual-mode :

- **Mode 1 (ce fichier)** : compare `openapi_spec_raw.json` baseline
  tracked vs champs déclarés `grandlivre.models.*` (Pydantic
  introspection). Détecte régression structurelle (baseline JSON corrompu
  OR modèles Pydantic perdent champs). Pre-commit local, zero-network
  cohérent CONTRIBUTING.md `respx zero-network obligatoire`.

- **Mode 2 (différé itération future)** : CI nightly opt-in
  `scripts/regenerate_openapi.sh` re-télécharge spec V2 → diff vs
  baseline tracked. Détecte drift amont API Pennylane. Implémentation
  workflow GitHub Actions séparé.

Doctrine §2.15 simplicity criterion : tests baseline structurel + 5
modèles critiques échantillon. Pas full diff schemas (effort > §2.18
critère succès initial, scope cap).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from grandlivre.models import (
    FiscalYear,
    Journal,
    LedgerAccount,
    MeUser,
    TrialBalanceAccount,
)

# Path absolu baseline tracked (résolu depuis test file location).
SPEC_PATH = Path(__file__).resolve().parents[2] / "openapi_spec_raw.json"

# Seuils baseline empiriques 2026-05-18 — sentinels détection régression
# structurelle (changements > N considérés breaking).
BASELINE_PATHS_COUNT = 120
BASELINE_SCHEMAS_COUNT = 115


@pytest.fixture(scope="module")
def spec() -> dict:
    """Load openapi_spec_raw.json baseline tracked."""
    return json.loads(SPEC_PATH.read_text())


def test_openapi_spec_loads_valid_json(spec: dict) -> None:
    """Sentinelle 1 : baseline JSON loads sans corruption."""
    assert isinstance(spec, dict)
    assert "openapi" in spec
    assert "info" in spec
    assert "paths" in spec
    assert "components" in spec


def test_openapi_spec_version_3(spec: dict) -> None:
    """Sentinelle 2 : spec version OpenAPI 3.x."""
    assert spec["openapi"].startswith("3.")


def test_openapi_spec_paths_count_baseline(spec: dict) -> None:
    """Sentinelle 3 : 120 paths baseline (régression si < 120 ou >> 120)."""
    paths = spec.get("paths", {})
    count = len(paths)
    assert count >= BASELINE_PATHS_COUNT, (
        f"Régression baseline : {count} paths détectés vs baseline "
        f"{BASELINE_PATHS_COUNT}. Vérifier baseline tracked corruption "
        f"ou actualiser BASELINE_PATHS_COUNT si extension API V2 amont."
    )


def test_openapi_spec_schemas_count_baseline(spec: dict) -> None:
    """Sentinelle 4 : 115 schemas baseline."""
    schemas = spec.get("components", {}).get("schemas", {})
    count = len(schemas)
    assert count >= BASELINE_SCHEMAS_COUNT, (
        f"Régression baseline : {count} schemas détectés vs baseline "
        f"{BASELINE_SCHEMAS_COUNT}. Vérifier baseline tracked corruption "
        f"ou actualiser BASELINE_SCHEMAS_COUNT si extension API V2 amont."
    )


def test_pydantic_model_me_user_fields() -> None:
    """Sentinelle 5 : MeUser modèle Pydantic conserve champs core."""
    fields = set(MeUser.model_fields.keys())
    expected_core = {"id", "first_name", "last_name", "email"}
    missing = expected_core - fields
    assert not missing, (
        f"Modèle MeUser perdrait champs core : {missing}. "
        f"Verify grandlivre/models/me.py preservation."
    )


def test_pydantic_model_fiscal_year_fields() -> None:
    """Sentinelle 6 : FiscalYear modèle Pydantic conserve champs core."""
    fields = set(FiscalYear.model_fields.keys())
    expected_core = {"id", "start", "finish", "status"}
    missing = expected_core - fields
    assert not missing, (
        f"Modèle FiscalYear perdrait champs core : {missing}. "
        f"Verify grandlivre/models/fiscal_years.py preservation."
    )


def test_pydantic_model_journal_fields() -> None:
    """Sentinelle 7 : Journal modèle Pydantic conserve champs core."""
    fields = set(Journal.model_fields.keys())
    expected_core = {"id", "code", "label"}
    missing = expected_core - fields
    assert not missing, (
        f"Modèle Journal perdrait champs core : {missing}. "
        f"Verify grandlivre/models/journals.py preservation."
    )


def test_pydantic_model_ledger_account_fields() -> None:
    """Sentinelle 8 : LedgerAccount modèle Pydantic conserve champs core."""
    fields = set(LedgerAccount.model_fields.keys())
    expected_core = {"id", "number", "label"}
    missing = expected_core - fields
    assert not missing, (
        f"Modèle LedgerAccount perdrait champs core : {missing}. "
        f"Verify grandlivre/models/ledger_accounts.py preservation."
    )


def test_pydantic_model_trial_balance_account_fields() -> None:
    """Sentinelle 9 : TrialBalanceAccount modèle Pydantic conserve champs core."""
    fields = set(TrialBalanceAccount.model_fields.keys())
    expected_core = {"number", "label", "debits", "credits"}
    missing = expected_core - fields
    assert not missing, (
        f"Modèle TrialBalanceAccount perdrait champs core : {missing}. "
        f"Verify grandlivre/models/trial_balance.py preservation."
    )
