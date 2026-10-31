"""Tests de la discipline de redaction des secrets.

Inspiration : la suite de tests du serveur hôte.

Le token Bearer ne doit JAMAIS apparaître dans :
  - le repr() ou str() d'une exception
  - le repr() ou str() de Settings
  - les attributs publics d'une exception construite via from_status
"""

from __future__ import annotations

import respx
from pydantic import SecretStr

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import (
    PennylaneError,
    from_status,
    redact_headers,
)

SECRET = "ULTRA-SECRET-TOKEN-DO-NOT-LEAK"  # pragma: allowlist secret


def test_settings_repr_does_not_leak() -> None:
    s = Settings(pennylane_api_token=SecretStr(SECRET))
    assert SECRET not in repr(s)
    assert SECRET not in str(s)


def test_redact_headers_authorization_value_replaced() -> None:
    headers = {"Authorization": f"Bearer {SECRET}", "Content-Type": "json"}
    out = redact_headers(headers)
    assert SECRET not in str(out)
    assert out["Authorization"] == "[REDACTED]"
    assert out["Content-Type"] == "json"


def test_pennylane_error_does_not_leak_in_repr() -> None:
    err = PennylaneError(
        "Boom",
        status_code=401,
        url="https://api.test/x",
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert SECRET not in repr(err)
    assert SECRET not in str(err)
    # Et accès direct à .headers déjà redacté.
    assert SECRET not in str(err.headers)


def test_from_status_does_not_leak() -> None:
    err = from_status(
        500,
        url="https://api.test/x",
        body=None,
        headers={"Authorization": f"Bearer {SECRET}", "X-Trace": "t1"},
    )
    assert SECRET not in repr(err)
    assert SECRET not in str(err.headers)
    assert err.headers["X-Trace"] == "t1"


@respx.mock
async def test_client_error_does_not_leak_token_in_exception() -> None:
    """Exécution end-to-end : token configuré, requête 401, exception ne doit
    JAMAIS contenir le token."""
    settings = Settings(
        pennylane_api_token=SecretStr(SECRET),
        pennylane_rate_limit=1000,
        pennylane_cabinet_id="cab-test",
    )
    respx.get("https://app.pennylane.com/api/external/v2/x").respond(401, text="nope")

    import pytest

    from grandlivre.exceptions import PennylaneAuthError

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await client.request("GET", "/x")

    err = excinfo.value
    assert SECRET not in repr(err)
    assert SECRET not in str(err)
    assert SECRET not in str(err.headers)
