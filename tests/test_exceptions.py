"""Tests de la hiérarchie d'exceptions et de la redaction des headers sensibles."""

from __future__ import annotations

from decimal import Decimal

import pytest

from grandlivre.exceptions import (
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
    from_status,
    redact_headers,
)


def test_redact_headers_none_returns_empty() -> None:
    assert redact_headers(None) == {}


def test_redact_headers_passes_non_sensitive() -> None:
    headers = {"Content-Type": "application/json", "X-Trace-Id": "abc-123"}
    result = redact_headers(headers)
    assert result == headers


@pytest.mark.parametrize(
    "header_name",
    ["Authorization", "authorization", "AUTHORIZATION"],
)
def test_redact_headers_case_insensitive_authorization(header_name: str) -> None:
    headers = {header_name: "Bearer secret-token-xyz"}
    result = redact_headers(headers)
    assert "secret-token-xyz" not in str(result)
    assert result[header_name] == "[REDACTED]"


@pytest.mark.parametrize(
    "sensitive_name",
    ["X-Api-Key", "Cookie", "Set-Cookie", "Proxy-Authorization"],
)
def test_redact_headers_redacts_all_sensitive(sensitive_name: str) -> None:
    headers = {sensitive_name: "value-to-hide"}
    result = redact_headers(headers)
    assert "value-to-hide" not in str(result)


def test_pennylane_error_str_with_status() -> None:
    err = PennylaneError("Boom", status_code=500, url="https://api.test/x")
    s = str(err)
    assert "[500]" in s
    assert "Boom" in s
    assert "https://api.test/x" in s


def test_pennylane_error_str_without_status() -> None:
    err = PennylaneError("Just a message")
    assert str(err) == "Just a message"


def test_pennylane_error_repr_contains_no_secret() -> None:
    err = PennylaneError(
        "Boom",
        status_code=401,
        url="https://api.test/x",
        headers={"Authorization": "Bearer SECRET-XYZ"},
    )
    r = repr(err)
    assert "SECRET-XYZ" not in r
    assert "REDACTED" in r


def test_fiscal_year_not_found_error() -> None:
    err = FiscalYearNotFoundError("2099")
    assert err.exercice == "2099"
    assert err.status_code is None
    assert "2099" in str(err)
    assert "fiscal_years" in err.message


def test_account_number_not_found_error() -> None:
    err = AccountNumberNotFoundError("411100241")
    assert err.account_number == "411100241"
    assert err.status_code is None
    assert "411100241" in str(err)


@pytest.mark.parametrize(
    "status, expected_cls",
    [
        (401, PennylaneAuthError),
        (403, PennylaneAuthError),
        (429, PennylaneRateLimitError),
        (500, PennylaneServerError),
        (502, PennylaneServerError),
        (503, PennylaneServerError),
        (599, PennylaneServerError),
        (400, PennylaneClientError),
        (404, PennylaneClientError),
        (422, PennylaneValidationError),
    ],
)
def test_from_status_maps_correctly(status: int, expected_cls: type[PennylaneError]) -> None:
    err = from_status(
        status,
        url="https://api.test/x",
        body=None,
        headers={"Authorization": "Bearer LeAkAbLe123"},
    )
    assert isinstance(err, expected_cls)
    assert err.status_code == status
    assert "LeAkAbLe123" not in repr(err)


def test_from_status_unusual_code() -> None:
    # 3xx ou 1xx ne correspondent à aucune catégorie standard,
    # mais doivent quand même produire une PennylaneError de base.
    err = from_status(301, url=None, body=None, headers=None)
    assert isinstance(err, PennylaneError)
    assert err.status_code == 301


# ---------------------------------------------------------------------------
# Tests hint actionable — pattern inspiré MemPalace `_no_palace()` (§3.4 audit).
# ---------------------------------------------------------------------------


def test_pennylane_error_hint_default_none() -> None:
    err = PennylaneError("Boom")
    assert err.hint is None


def test_pennylane_error_hint_stored() -> None:
    err = PennylaneError("Boom", hint="Set X to Y")
    assert err.hint == "Set X to Y"


def test_pennylane_error_str_includes_hint() -> None:
    err = PennylaneError("Boom", status_code=401, url="https://x", hint="Refresh token")
    s = str(err)
    assert "Refresh token" in s
    assert "hint:" in s


def test_pennylane_error_str_without_hint_unchanged() -> None:
    err = PennylaneError("Boom", status_code=500, url="https://x")
    s = str(err)
    assert "hint:" not in s
    assert "[500]" in s


def test_pennylane_error_repr_includes_hint() -> None:
    err = PennylaneError("Boom", hint="Do this")
    r = repr(err)
    assert "hint='Do this'" in r


@pytest.mark.parametrize(
    "status, hint_marker",
    [
        (401, "PENNYLANE_API_TOKEN"),
        (403, "PENNYLANE_API_TOKEN"),
        (429, "tenacity"),
        (500, "support Pennylane"),
        (503, "support Pennylane"),
        (400, "payload"),
        (404, "payload"),
        (422, "422"),
    ],
)
def test_from_status_injects_hint(status: int, hint_marker: str) -> None:
    err = from_status(status, url="https://api.test/x", body=None, headers=None)
    assert err.hint is not None
    assert hint_marker in err.hint


def test_from_status_unusual_code_has_hint() -> None:
    err = from_status(301, url=None, body=None, headers=None)
    assert err.hint is not None
    assert "1xx/3xx" in err.hint or "hors plage" in err.hint


def test_fiscal_year_not_found_has_hint() -> None:
    err = FiscalYearNotFoundError("2099")
    assert err.hint is not None
    assert "fiscal_years" in err.hint


def test_account_number_not_found_has_hint() -> None:
    err = AccountNumberNotFoundError("411100241")
    assert err.hint is not None
    assert "ledger_accounts" in err.hint


def test_fec_export_failed_default_hint() -> None:
    err = FECExportFailedError("Job failed")
    assert err.hint is not None
    assert "FEC" in err.hint


def test_fec_export_failed_hint_override() -> None:
    err = FECExportFailedError("Job failed", hint="Custom hint XYZ")
    assert err.hint == "Custom hint XYZ"


# ---------------------------------------------------------------------------
# Module write (ADR) — UnbalancedEntryError (pré-POST) + 422 PennylaneValidationError.
# ---------------------------------------------------------------------------


def test_unbalanced_entry_error() -> None:
    err = UnbalancedEntryError(Decimal("100.00"), Decimal("90.00"))
    assert err.debit_sum == Decimal("100.00")
    assert err.credit_sum == Decimal("90.00")
    assert err.balance == Decimal("10.00")
    assert err.status_code is None  # erreur métier, pré-POST (jamais HTTP)
    assert "100.00" in str(err) and "90.00" in str(err)
    assert err.hint is not None and "équilibr" in err.hint.lower()


def test_unbalanced_entry_error_balanced_is_zero() -> None:
    err = UnbalancedEntryError(Decimal("50.00"), Decimal("50.00"))
    assert err.balance == Decimal("0.00")


def test_from_status_422_is_validation_error() -> None:
    err = from_status(422, url="https://x", body='{"error":"journal closed"}', headers=None)
    assert isinstance(err, PennylaneValidationError)
    # Liskov : reste attrapable comme une erreur client 4xx (handlers existants).
    assert isinstance(err, PennylaneClientError)
    assert err.status_code == 422
    assert err.body == '{"error":"journal closed"}'
    assert err.hint is not None
