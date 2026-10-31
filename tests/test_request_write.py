"""Tests de PennylaneClient.request_write — chemin write-safe (ADR).

Vérifie l'interdiction du re-POST aveugle : seuls 429 (rejeté avant traitement) +
erreurs de connexion (bytes non envoyés) sont retentés ; tout 5xx / timeout
post-envoi est PROPAGÉ (l'appelant transitionne IN_DOUBT + read-back, jamais
re-POST). Zéro réseau (respx).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import (
    PennylaneClientError,
    PennylaneServerError,
    PennylaneValidationError,
)

_BASE = "https://app.pennylane.com/api/external/v2"
_OK_BODY = {"id": 1, "ledger_entry_lines": []}


@respx.mock
async def test_request_write_201_returns_body(settings: Settings) -> None:
    route = respx.post(f"{_BASE}/ledger_entries").respond(201, json=_OK_BODY)
    async with PennylaneClient(settings=settings) as client:
        body = await client.request_write("POST", "/ledger_entries", json={"x": 1})
    assert body == _OK_BODY
    assert route.call_count == 1


@respx.mock
async def test_request_write_429_then_201_retried(settings: Settings) -> None:
    # 429 = rejeté avant traitement (pas d'écriture) -> retry sûr.
    route = respx.post(f"{_BASE}/ledger_entries").mock(
        side_effect=[httpx.Response(429), httpx.Response(201, json=_OK_BODY)]
    )
    async with PennylaneClient(settings=settings) as client:
        body = await client.request_write("POST", "/ledger_entries", json={"x": 1})
    assert body == _OK_BODY
    assert route.call_count == 2


@respx.mock
async def test_request_write_connect_error_then_201_retried(settings: Settings) -> None:
    # ConnectError = connexion jamais établie (bytes non envoyés) -> retry sûr.
    route = respx.post(f"{_BASE}/ledger_entries").mock(
        side_effect=[httpx.ConnectError("connexion refusée"), httpx.Response(201, json=_OK_BODY)]
    )
    async with PennylaneClient(settings=settings) as client:
        body = await client.request_write("POST", "/ledger_entries", json={"x": 1})
    assert body == _OK_BODY
    assert route.call_count == 2


@respx.mock
async def test_request_write_5xx_not_retried_raises_server_error(settings: Settings) -> None:
    # 5xx APRÈS envoi : ambigu (le serveur a pu committer) -> PROPAGÉ, JAMAIS re-POST.
    route = respx.post(f"{_BASE}/ledger_entries").respond(503)
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneServerError):
            await client.request_write("POST", "/ledger_entries", json={"x": 1})
    assert route.call_count == 1  # pas de re-POST aveugle


@respx.mock
async def test_request_write_read_timeout_not_retried(settings: Settings) -> None:
    # Timeout post-envoi : état d'écriture incertain -> PROPAGÉ, JAMAIS re-POST.
    route = respx.post(f"{_BASE}/ledger_entries").mock(side_effect=httpx.ReadTimeout("timeout"))
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.request_write("POST", "/ledger_entries", json={"x": 1})
    assert route.call_count == 1


@respx.mock
async def test_request_write_422_raises_validation_error(settings: Settings) -> None:
    # 422 = refus déterministe (journal fermé, compte inexistant) -> FAILED, pas de retry.
    route = respx.post(f"{_BASE}/ledger_entries").respond(
        422, json={"error": "journal fermé", "status": 422}
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneValidationError):
            await client.request_write("POST", "/ledger_entries", json={"x": 1})
    assert route.call_count == 1


@respx.mock
async def test_request_write_400_raises_client_error(settings: Settings) -> None:
    respx.post(f"{_BASE}/ledger_entries").respond(400, json={"error": "bad", "status": 400})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await client.request_write("POST", "/ledger_entries", json={"x": 1})
