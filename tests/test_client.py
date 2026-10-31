"""Tests du PennylaneClient (httpx async + headers + rate limit + retry + redaction).

Inspiration : la suite de tests du serveur hôte.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import (
    PennylaneAuthError,
    PennylaneClientError,
)


@respx.mock
async def test_request_injects_bearer_and_2026_header(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/ping").respond(
        200, json={"ok": True}
    )

    async with PennylaneClient(settings=settings) as client:
        await client.request("GET", "/ping")

    assert route.called
    sent_headers = route.calls.last.request.headers
    assert sent_headers["authorization"] == "Bearer test-token-NOT-A-REAL-SECRET"
    assert sent_headers["x-use-2026-api-changes"] == "true"
    assert sent_headers["accept"] == "application/json"


@respx.mock
async def test_request_omits_2026_header_when_disabled(
    settings_no_2026: Settings,
) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/ping").respond(
        200, json={"ok": True}
    )

    async with PennylaneClient(settings=settings_no_2026) as client:
        await client.request("GET", "/ping")

    sent_headers = route.calls.last.request.headers
    assert "x-use-2026-api-changes" not in sent_headers


@respx.mock
async def test_401_raises_auth_error_with_redacted_headers(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/ping").respond(
        401, json={"error": "invalid token"}
    )

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await client.request("GET", "/ping")

    err = excinfo.value
    assert err.status_code == 401
    # Aucun header sensible n'a fui dans l'exception.
    for value in err.headers.values():
        assert "test-token-NOT-A-REAL-SECRET" not in value
    assert "test-token-NOT-A-REAL-SECRET" not in repr(err)
    assert "test-token-NOT-A-REAL-SECRET" not in str(err)


@respx.mock
async def test_403_raises_auth_error(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/ping").respond(
        403, json={"error": "forbidden scope"}
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await client.request("GET", "/ping")


@respx.mock
async def test_400_raises_client_error_not_retried(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/ping").respond(
        400, json={"error": "bad request"}
    )

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await client.request("GET", "/ping")

    assert route.call_count == 1  # 4xx hors 429 non retried


@respx.mock
async def test_retry_on_429_then_success(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/ping").mock(
        side_effect=[
            httpx.Response(429, json={"error": "rate limited"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async with PennylaneClient(settings=settings) as client:
        result = await client.request("GET", "/ping")

    assert result == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_retry_on_503_then_success(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/ping").mock(
        side_effect=[
            httpx.Response(503, json={"error": "unavailable"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async with PennylaneClient(settings=settings) as client:
        result = await client.request("GET", "/ping")

    assert result == {"ok": True}
    assert route.call_count == 2


@respx.mock
async def test_request_with_params_and_json(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/x").respond(
        200, json={"got": True}
    )

    async with PennylaneClient(settings=settings) as client:
        await client.request("GET", "/x", params={"a": "1", "b": "2"})

    sent_url = route.calls.last.request.url
    assert sent_url.params.get("a") == "1"
    assert sent_url.params.get("b") == "2"


@respx.mock
async def test_url_query_redacted_from_exception(settings: Settings) -> None:
    """Une exception construite à partir d'une réponse erreur ne doit pas révéler
    la query string (qui peut contenir des filtres clients sensibles)."""
    respx.get("https://app.pennylane.com/api/external/v2/x").respond(400, text="oops")

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError) as excinfo:
            await client.request("GET", "/x", params={"client_secret_filter": "v"})

    assert "client_secret_filter" not in (excinfo.value.url or "")


@respx.mock
async def test_external_http_client_lifecycle_not_managed(settings: Settings) -> None:
    """Si un http_client externe est injecté, PennylaneClient ne le ferme pas."""
    respx.get("https://app.pennylane.com/api/external/v2/ping").respond(200, json={"ok": True})

    external = httpx.AsyncClient(base_url=settings.pennylane_base_url)
    async with PennylaneClient(settings=settings, http_client=external) as client:
        await client.request("GET", "/ping")
    # Le client externe doit toujours être ouvert.
    assert not external.is_closed
    await external.aclose()


def test_client_settings_property(settings: Settings) -> None:
    client = PennylaneClient(settings=settings)
    assert client.settings is settings
