"""Tests de LedgerAccountsResource (v0.3 B.4).

Schéma `/ledger_accounts` capturé empiriquement 2026-05-12 SID
`6afe82d0` : 10 champs canoniques (`id, number, label, vat_rate,
country_alpha2, type, enabled, letterable, created_at, updated_at`).
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import (
    AccountNumberNotFoundError,
    PennylaneAuthError,
    PennylaneClientError,
)
from grandlivre.models.ledger_accounts import LedgerAccount
from grandlivre.resources.ledger_accounts import LedgerAccountsResource

_BASE = "https://app.pennylane.com/api/external/v2"


def _account(aid: int, number: str = "411100", label: str | None = None) -> dict:
    return {
        "id": aid,
        "number": number,
        "label": label or f"Compte {number}",
        "vat_rate": "DE_200",
        "country_alpha2": "any",
        "type": "reserved",
        "enabled": True,
        "letterable": True,
        "created_at": "2026-04-17T18:50:41.104046Z",
        "updated_at": "2026-04-17T18:50:41.102451Z",
    }


# ─── Chemin nominal ──────────────────────────────────────────────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_accounts").respond(
        200,
        json={
            "items": [
                _account(1, "411100", "Clients - ventes"),
                _account(2, "401100", "Fournisseurs - achats"),
                _account(3, "512000", "Banque"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerAccountsResource(client).list()
    assert len(items) == 3
    assert all(isinstance(it, LedgerAccount) for it in items)
    assert items[0].id == 1
    assert items[0].number == "411100"
    assert items[0].label == "Clients - ventes"
    assert items[0].enabled is True
    assert items[2].number == "512000"


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_accounts").respond(
        200,
        json={"items": [_account(1), _account(2, "606300")]},
    )
    async with PennylaneClient(settings=settings) as client:
        nums = []
        async for a in LedgerAccountsResource(client).iterate():
            nums.append(a.number)
    assert nums == ["411100", "606300"]


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_accounts").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [_account(1)], "next_cursor": "c2"},
            ),
            httpx.Response(
                200,
                json={"items": [_account(2, "401100")], "next_cursor": None},
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerAccountsResource(client).list()
    assert [it.id for it in items] == [1, 2]


# ─── get(id) ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_by_id_returns_account(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_accounts/42").respond(200, json=_account(42, "411100", "Clients"))
    async with PennylaneClient(settings=settings) as client:
        a = await LedgerAccountsResource(client).get(42)
    assert a.id == 42
    assert a.number == "411100"


@respx.mock
async def test_get_id_404_propagates_client_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_accounts/99999").respond(404, json={"error": "not found"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await LedgerAccountsResource(client).get(99999)


# ─── get_by_number(number) ───────────────────────────────────────────────────


@respx.mock
async def test_get_by_number_returns_account(settings: Settings) -> None:
    route = respx.get(f"{_BASE}/ledger_accounts").respond(
        200, json={"items": [_account(7, "411100241", "Client X")]}
    )
    async with PennylaneClient(settings=settings) as client:
        a = await LedgerAccountsResource(client).get_by_number("411100241")
    assert a.id == 7
    assert a.number == "411100241"
    # Vérification filter envoyé en query
    filter_str = route.calls.last.request.url.params["filter"]
    clauses = json.loads(filter_str)
    assert clauses == [{"field": "number", "operator": "eq", "value": "411100241"}]


@respx.mock
async def test_get_by_number_raises_when_unknown(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_accounts").respond(200, json={"items": []})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(AccountNumberNotFoundError) as excinfo:
            await LedgerAccountsResource(client).get_by_number("999999")
    assert excinfo.value.account_number == "999999"


# ─── Résilience extra="allow" + optionnels ───────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    payload = {"items": [{**_account(1), "currency": "EUR", "analytical_dimensions": ["d1"]}]}
    respx.get(f"{_BASE}/ledger_accounts").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerAccountsResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["currency"] == "EUR"
    assert dumped["analytical_dimensions"] == ["d1"]


@respx.mock
async def test_minimal_payload_with_only_required_fields(settings: Settings) -> None:
    """`id` et `number` seuls suffisent. Tous les autres optionnels → None."""
    payload = {"items": [{"id": 1, "number": "411100"}]}
    respx.get(f"{_BASE}/ledger_accounts").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerAccountsResource(client).list()
    assert items[0].id == 1
    assert items[0].number == "411100"
    assert items[0].label is None
    assert items[0].vat_rate is None
    assert items[0].enabled is None


# ─── Propagation erreurs ─────────────────────────────────────────────────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_accounts").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await LedgerAccountsResource(client).list()
