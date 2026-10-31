"""Tests de LedgerEntriesGroupedResource (v0.3 B.5).

Schéma `/ledger_entries` capturé empiriquement 2026-05-12 SID `6afe82d0`
sur dossier test : items 13 champs avec `journal` objet + `journal_id`
legacy coexistants (Sunset 2026 en cours).

Distinct de `LedgerEntriesResource` v0.1 (`/ledger_entry_lines` plat).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError, PennylaneClientError
from grandlivre.models.ledger_entries_grouped import LedgerEntryGrouped
from grandlivre.resources.ledger_entries_grouped import (
    DEFAULT_HARD_CAP,
    LedgerEntriesGroupedResource,
)

_BASE = "https://app.pennylane.com/api/external/v2"


def _entry(eid: int, journal_id: int = 1000, label: str | None = None) -> dict:
    return {
        "id": eid,
        "label": label or f"Facture F-{eid}",
        "date": "2026-05-11",
        "journal_id": journal_id,
        "journal": {
            "id": journal_id,
            "url": f"{_BASE}/journals/{journal_id}",
        },
        "invoice_number": f"F-2026-{eid}",
        "piece_number": f"P-{eid}",
        "due_date": "2026-06-10",
        "attachment": None,
        "categories": [],
        "ledger_attachment_filename": None,
        "created_at": "2026-05-11T03:57:08.653823Z",
        "updated_at": "2026-05-11T03:57:08.721568Z",
    }


# ─── Chemin nominal ──────────────────────────────────────────────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_entries").respond(
        200,
        json={
            "items": [_entry(1), _entry(2, journal_id=2000), _entry(3)],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerEntriesGroupedResource(client).list()
    assert len(items) == 3
    assert all(isinstance(it, LedgerEntryGrouped) for it in items)
    assert items[0].id == 1
    assert items[0].journal_id == 1000
    assert items[0].journal == {
        "id": 1000,
        "url": f"{_BASE}/journals/1000",
    }
    assert items[1].journal_id == 2000


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_entries").respond(200, json={"items": [_entry(1), _entry(2)]})
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for e in LedgerEntriesGroupedResource(client).iterate():
            ids.append(e.id)
    assert ids == [1, 2]


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_entries").mock(
        side_effect=[
            httpx.Response(200, json={"items": [_entry(1)], "next_cursor": "c2"}),
            httpx.Response(200, json={"items": [_entry(2)], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerEntriesGroupedResource(client).list()
    assert [it.id for it in items] == [1, 2]


# ─── paginate avec cap dur ───────────────────────────────────────────────────


@respx.mock
async def test_paginate_truncated_returns_next_cursor(
    settings: Settings,
) -> None:
    """600 items, cap=500 → truncated=True + next_cursor."""
    items = [_entry(i) for i in range(600)]
    respx.get(f"{_BASE}/ledger_entries").respond(
        200, json={"items": items, "next_cursor": "abc-123"}
    )
    async with PennylaneClient(settings=settings) as client:
        page, truncated, next_cursor = await LedgerEntriesGroupedResource(client).paginate(cap=500)
    assert truncated is True
    assert next_cursor == "abc-123"
    assert len(page) == 500


@respx.mock
async def test_paginate_below_cap_returns_no_cursor(settings: Settings) -> None:
    """250 items, cap=500 → truncated=False."""
    respx.get(f"{_BASE}/ledger_entries").respond(
        200,
        json={
            "items": [_entry(i) for i in range(250)],
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        page, truncated, next_cursor = await LedgerEntriesGroupedResource(client).paginate()
    assert truncated is False
    assert next_cursor is None
    assert len(page) == 250


async def test_default_hard_cap_is_500() -> None:
    assert DEFAULT_HARD_CAP == 500


# ─── get(id) ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_by_id_returns_entry(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_entries/42").respond(200, json=_entry(42))
    async with PennylaneClient(settings=settings) as client:
        e = await LedgerEntriesGroupedResource(client).get(42)
    assert e.id == 42
    assert e.invoice_number == "F-2026-42"


@respx.mock
async def test_get_id_404_propagates_client_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_entries/99999").respond(404, json={"error": "not found"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await LedgerEntriesGroupedResource(client).get(99999)


# ─── Résilience extra="allow" + optionnels ───────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    payload = {"items": [{**_entry(1), "balance": "0.00", "validated_at": "2026-05-12T01:00:00Z"}]}
    respx.get(f"{_BASE}/ledger_entries").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerEntriesGroupedResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["balance"] == "0.00"
    assert dumped["validated_at"] == "2026-05-12T01:00:00Z"


@respx.mock
async def test_minimal_payload(settings: Settings) -> None:
    """`id` seul suffit. Tous les autres optionnels → None."""
    payload = {"items": [{"id": 7}]}
    respx.get(f"{_BASE}/ledger_entries").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await LedgerEntriesGroupedResource(client).list()
    assert items[0].id == 7
    assert items[0].journal_id is None
    assert items[0].label is None
    assert items[0].invoice_number is None


# ─── Propagation erreurs ─────────────────────────────────────────────────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/ledger_entries").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await LedgerEntriesGroupedResource(client).list()
