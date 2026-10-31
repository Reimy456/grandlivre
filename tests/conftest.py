"""Fixtures partagées pour grandlivre/tests/.

Inspiration : la suite de tests du serveur hôte.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from grandlivre.config import Settings
from grandlivre.rate_limit import reset_rate_limiters


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> None:
    """Vide le registre des limiters in-process entre les tests."""
    reset_rate_limiters()


@pytest.fixture()
def settings() -> Settings:
    """Settings minimaux pour les tests, token sentinel non commité."""
    return Settings(
        pennylane_api_token=SecretStr("test-token-NOT-A-REAL-SECRET"),
        pennylane_base_url="https://app.pennylane.com/api/external/v2",
        pennylane_rate_limit=1000,  # élevé pour ne pas ralentir les tests
        pennylane_cabinet_id="cab-test",
        pennylane_use_2026_api_changes=True,
    )


@pytest.fixture()
def settings_no_2026(settings: Settings) -> Settings:
    """Variante avec header 2026 désactivé (test d'opt-out)."""
    return settings.model_copy(update={"pennylane_use_2026_api_changes": False})


@pytest.fixture(autouse=True)
def _disable_tenacity_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Court-circuite les sleeps tenacity / asyncio pour des tests rapides."""
    import asyncio

    async def _no_sleep(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
