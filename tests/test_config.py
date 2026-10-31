"""Tests de la configuration Settings."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from grandlivre.config import Settings, get_settings


def test_settings_defaults() -> None:
    s = Settings()
    assert s.pennylane_base_url == "https://app.pennylane.com/api/external/v2"
    assert s.pennylane_rate_limit == 5
    assert s.pennylane_cabinet_id == "default"
    assert s.pennylane_use_2026_api_changes is True
    assert s.pennylane_request_timeout_seconds == 30.0


def test_settings_token_is_secret_str() -> None:
    s = Settings(pennylane_api_token=SecretStr("xyz-token"))
    assert s.pennylane_api_token.get_secret_value() == "xyz-token"
    # Le token brut ne doit JAMAIS apparaître dans repr ni str.
    assert "xyz-token" not in repr(s)
    assert "xyz-token" not in str(s)


def test_settings_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PENNYLANE_API_TOKEN", "env-token")
    monkeypatch.setenv("PENNYLANE_RATE_LIMIT", "4")
    monkeypatch.setenv("PENNYLANE_CABINET_ID", "cab42")
    monkeypatch.setenv("PENNYLANE_USE_2026_API_CHANGES", "false")
    s = Settings()
    assert s.pennylane_api_token.get_secret_value() == "env-token"
    assert s.pennylane_rate_limit == 4
    assert s.pennylane_cabinet_id == "cab42"
    assert s.pennylane_use_2026_api_changes is False


def test_get_settings_returns_fresh_instance() -> None:
    a = get_settings()
    b = get_settings()
    assert a is not b  # Pas de cache LRU
    assert a.pennylane_base_url == b.pennylane_base_url
