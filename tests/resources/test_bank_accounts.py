"""Tests de BankAccountsResource (v0.3 B.14).

**Bloqueur scope sandbox** : Company Token actuel n'a pas
`bank_accounts:readonly` (HTTP 403 capturé empiriquement 2026-05-12
SID `6afe82d0`). Corollaire bloqueur B.7. Tests respx mockent les
réponses — pas de smoke sandbox tant que scope absent.

Schéma `BankAccount` sourcé `openapi_spec_raw.json` zone
`/api/external/v2/bank_accounts` (9 champs canoniques).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError, PennylaneClientError
from grandlivre.models.bank_accounts import BankAccount
from grandlivre.resources.bank_accounts import (
    DEFAULT_HARD_CAP,
    BankAccountsResource,
)

_BASE = "https://app.pennylane.com/api/external/v2"


def _bank_account(bid: int, name: str = "Compte courant", balance: str = "1000.00") -> dict:
    """Fixture synthétique cohérente schéma OpenAPI."""
    return {
        "id": bid,
        "name": name,
        "currency": "EUR",
        "balance": balance,
        "created_at": "2026-05-11T10:00:00Z",
        "updated_at": "2026-05-11T10:00:00Z",
        "bank_establishment": {"id": 100},
        "journal": {"id": 1000, "url": "/api/external/v2/journals/1000"},
        "ledger_account": {
            "id": 5120,
            "url": "/api/external/v2/ledger_accounts/5120",
        },
    }


# ─── Bloqueur scope sandbox (HTTP 403) — cas reproductible empiriquement ─────


@respx.mock
async def test_propagates_403_when_scope_missing(settings: Settings) -> None:
    """Cas empirique sandbox actuel : Company Token sans scope
    `bank_accounts:readonly` → HTTP 403 (`PennylaneAuthError`)."""
    respx.get(f"{_BASE}/bank_accounts").respond(
        403,
        json={
            "status": 403,
            "error": (
                'Access to this resource requires scope "bank_accounts:all bank_accounts:readonly".'
            ),
        },
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await BankAccountsResource(client).list()
    assert excinfo.value.status_code == 403


# ─── Chemin nominal (post-renouvellement token avec scope) ───────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    respx.get(f"{_BASE}/bank_accounts").respond(
        200,
        json={
            "items": [
                _bank_account(1, "Compte courant pro", "5000.00"),
                _bank_account(2, "Compte épargne", "12500.50"),
                _bank_account(3, "Compte devise USD", "300.00"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await BankAccountsResource(client).list()
    assert len(items) == 3
    assert all(isinstance(it, BankAccount) for it in items)
    assert items[0].id == 1
    assert items[0].name == "Compte courant pro"
    assert items[0].balance == "5000.00"
    assert items[0].currency == "EUR"
    assert items[1].balance == "12500.50"
    assert items[2].name == "Compte devise USD"


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    respx.get(f"{_BASE}/bank_accounts").respond(
        200, json={"items": [_bank_account(1), _bank_account(2)]}
    )
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for a in BankAccountsResource(client).iterate():
            ids.append(a.id)
    assert ids == [1, 2]


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{_BASE}/bank_accounts").mock(
        side_effect=[
            httpx.Response(200, json={"items": [_bank_account(1)], "next_cursor": "c2"}),
            httpx.Response(200, json={"items": [_bank_account(2)], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await BankAccountsResource(client).list()
    assert [it.id for it in items] == [1, 2]


# ─── paginate avec cap dur ───────────────────────────────────────────────────


@respx.mock
async def test_paginate_truncated_returns_next_cursor(
    settings: Settings,
) -> None:
    items = [_bank_account(i) for i in range(600)]
    respx.get(f"{_BASE}/bank_accounts").respond(200, json={"items": items, "next_cursor": "abc"})
    async with PennylaneClient(settings=settings) as client:
        page, truncated, nc = await BankAccountsResource(client).paginate(cap=500)
    assert truncated is True
    assert nc == "abc"
    assert len(page) == 500


async def test_default_hard_cap_is_500() -> None:
    assert DEFAULT_HARD_CAP == 500


# ─── get(id) ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_by_id_returns_bank_account(settings: Settings) -> None:
    respx.get(f"{_BASE}/bank_accounts/42").respond(200, json=_bank_account(42))
    async with PennylaneClient(settings=settings) as client:
        a = await BankAccountsResource(client).get(42)
    assert a.id == 42
    assert a.bank_establishment == {"id": 100}
    assert a.ledger_account == {
        "id": 5120,
        "url": "/api/external/v2/ledger_accounts/5120",
    }


@respx.mock
async def test_get_id_404_propagates_client_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/bank_accounts/99999").respond(404, json={"error": "not found"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await BankAccountsResource(client).get(99999)


# ─── Résilience extra="allow" + optionnels ───────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    payload = {
        "items": [
            {
                **_bank_account(1),
                "iban": "FR7630003000000000000000000",  # champ inattendu
                "bic": "SOGEFRPP",
            }
        ]
    }
    respx.get(f"{_BASE}/bank_accounts").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await BankAccountsResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["iban"] == "FR7630003000000000000000000"
    assert dumped["bic"] == "SOGEFRPP"


@respx.mock
async def test_minimal_payload_with_only_id(settings: Settings) -> None:
    """`id` seul suffit. Tous les autres optionnels → None."""
    payload = {"items": [{"id": 7}]}
    respx.get(f"{_BASE}/bank_accounts").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await BankAccountsResource(client).list()
    assert items[0].id == 7
    assert items[0].name is None
    assert items[0].balance is None
    assert items[0].bank_establishment is None


# ─── Propagation 401 (token invalide, distinct du 403 scope manquant) ────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/bank_accounts").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await BankAccountsResource(client).list()
    assert excinfo.value.status_code == 401
