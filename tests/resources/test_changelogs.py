"""Tests de ChangelogsResource (v0.3 B.8).

Schéma `/changelogs/*` uniforme capturé empiriquement 2026-05-12
SID `6afe82d0` : items `{id, operation, created_at, updated_at,
processed_at}`. `operation` ∈ {insert, update, delete}.

**Bloqueur scope sub-endpoint `transactions`** : corollaire B.7,
HTTP 403 capturé empiriquement (`requires scope
transactions:readonly`). Tests respx couvrent les 3 sous-endpoints
y compris le cas 403 reproductible.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError
from grandlivre.models.changelogs import ChangelogEntry
from grandlivre.resources.changelogs import ChangelogsResource

_BASE = "https://app.pennylane.com/api/external/v2"


def _entry(eid: int, op: str = "insert") -> dict:
    return {
        "id": eid,
        "operation": op,
        "created_at": f"2026-04-17T06:02:{eid:02d}.192133Z",
        "updated_at": f"2026-04-17T06:02:{eid:02d}.192536Z",
        "processed_at": f"2026-04-17T06:02:{eid + 1:02d}.316983Z",
    }


# ─── ledger_entry_lines() ────────────────────────────────────────────────────


@respx.mock
async def test_ledger_entry_lines_returns_all(settings: Settings) -> None:
    respx.get(f"{_BASE}/changelogs/ledger_entry_lines").respond(
        200,
        json={
            "items": [_entry(1), _entry(2, "update"), _entry(3, "delete")],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await ChangelogsResource(client).ledger_entry_lines()
    assert len(items) == 3
    assert all(isinstance(it, ChangelogEntry) for it in items)
    assert items[0].operation == "insert"
    assert items[1].operation == "update"
    assert items[2].operation == "delete"


@respx.mock
async def test_iterate_ledger_entry_lines_yields(settings: Settings) -> None:
    respx.get(f"{_BASE}/changelogs/ledger_entry_lines").respond(
        200, json={"items": [_entry(1), _entry(2)]}
    )
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for e in ChangelogsResource(client).iterate_ledger_entry_lines():
            ids.append(e.id)
    assert ids == [1, 2]


@respx.mock
async def test_ledger_entry_lines_pagination(settings: Settings) -> None:
    respx.get(f"{_BASE}/changelogs/ledger_entry_lines").mock(
        side_effect=[
            httpx.Response(200, json={"items": [_entry(1)], "next_cursor": "c2"}),
            httpx.Response(200, json={"items": [_entry(2)], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await ChangelogsResource(client).ledger_entry_lines()
    assert [it.id for it in items] == [1, 2]


# ─── supplier_invoices() ─────────────────────────────────────────────────────


@respx.mock
async def test_supplier_invoices_returns_all(settings: Settings) -> None:
    respx.get(f"{_BASE}/changelogs/supplier_invoices").respond(
        200,
        json={
            "items": [_entry(10, "delete"), _entry(11, "insert")],
            "has_more": False,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await ChangelogsResource(client).supplier_invoices()
    assert len(items) == 2
    assert items[0].id == 10
    assert items[0].operation == "delete"


@respx.mock
async def test_iterate_supplier_invoices_yields(settings: Settings) -> None:
    respx.get(f"{_BASE}/changelogs/supplier_invoices").respond(200, json={"items": [_entry(1)]})
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for e in ChangelogsResource(client).iterate_supplier_invoices():
            ids.append(e.id)
    assert ids == [1]


# ─── transactions() — bloqueur scope B.7 corollaire ──────────────────────────


@respx.mock
async def test_transactions_propagates_403_when_scope_missing(
    settings: Settings,
) -> None:
    """Cas empirique sandbox actuel : corollaire B.7 — `/changelogs/transactions`
    retourne 403 si scope `transactions:readonly` absent."""
    respx.get(f"{_BASE}/changelogs/transactions").respond(
        403,
        json={
            "status": 403,
            "error": (
                'Access to this resource requires scope "transactions:all transactions:readonly".'
            ),
        },
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await ChangelogsResource(client).transactions()
    assert excinfo.value.status_code == 403


@respx.mock
async def test_transactions_returns_items_post_scope_granted(
    settings: Settings,
) -> None:
    """Cas post-renouvellement (scope `transactions:readonly` ajouté) :
    `/changelogs/transactions` retourne items uniformes."""
    respx.get(f"{_BASE}/changelogs/transactions").respond(
        200,
        json={"items": [_entry(100, "insert"), _entry(101, "update")]},
    )
    async with PennylaneClient(settings=settings) as client:
        items = await ChangelogsResource(client).transactions()
    assert len(items) == 2
    assert items[0].id == 100
    assert items[1].operation == "update"


@respx.mock
async def test_iterate_transactions_yields(settings: Settings) -> None:
    respx.get(f"{_BASE}/changelogs/transactions").respond(200, json={"items": [_entry(200)]})
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for e in ChangelogsResource(client).iterate_transactions():
            ids.append(e.id)
    assert ids == [200]


# ─── Résilience extra="allow" + optionnels ───────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    payload = {"items": [{**_entry(1), "actor": "user-42", "diff": {"x": "y"}}]}
    respx.get(f"{_BASE}/changelogs/ledger_entry_lines").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await ChangelogsResource(client).ledger_entry_lines()
    dumped = items[0].model_dump()
    assert dumped["actor"] == "user-42"
    assert dumped["diff"] == {"x": "y"}


@respx.mock
async def test_minimal_payload_with_only_id(settings: Settings) -> None:
    """`id` seul suffit. Tous les autres optionnels → None."""
    respx.get(f"{_BASE}/changelogs/ledger_entry_lines").respond(200, json={"items": [{"id": 7}]})
    async with PennylaneClient(settings=settings) as client:
        items = await ChangelogsResource(client).ledger_entry_lines()
    assert items[0].id == 7
    assert items[0].operation is None
    assert items[0].processed_at is None


# ─── Propagation 401 ─────────────────────────────────────────────────────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/changelogs/ledger_entry_lines").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await ChangelogsResource(client).ledger_entry_lines()
    assert excinfo.value.status_code == 401
