"""Tests de JournalsResource (v0.3 B.3).

Schéma `/journals` capturé empiriquement 2026-05-12 SID `6afe82d0` :
    {items: [{id, code, label, type}], has_more, next_cursor}

Schéma `/journals/{id}` identique au schéma item liste (vérifié curl).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError, PennylaneClientError
from grandlivre.models.journals import Journal
from grandlivre.resources.journals import JournalsResource

_BASE = "https://app.pennylane.com/api/external/v2"


def _journal(jid: int, code: str = "BQ", label: str | None = None, jtype: str = "finances") -> dict:
    return {
        "id": jid,
        "code": code,
        "label": label or f"Journal {code}",
        "type": jtype,
    }


# ─── Chemin nominal ──────────────────────────────────────────────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    respx.get(f"{_BASE}/journals").respond(
        200,
        json={
            "items": [
                _journal(1, "BQ", "Banque", "finances"),
                _journal(2, "VT", "Ventes", "sales"),
                _journal(3, "AC", "Achats", "purchases"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await JournalsResource(client).list()
    assert len(items) == 3
    assert all(isinstance(it, Journal) for it in items)
    assert items[0].id == 1
    assert items[0].code == "BQ"
    assert items[0].label == "Banque"
    assert items[0].type == "finances"
    assert items[2].code == "AC"


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    respx.get(f"{_BASE}/journals").respond(
        200,
        json={"items": [_journal(1, "BQ"), _journal(2, "VT")]},
    )
    async with PennylaneClient(settings=settings) as client:
        codes = []
        async for j in JournalsResource(client).iterate():
            codes.append(j.code)
    assert codes == ["BQ", "VT"]


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{_BASE}/journals").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [_journal(1, "BQ")], "next_cursor": "c2"},
            ),
            httpx.Response(
                200,
                json={"items": [_journal(2, "VT")], "next_cursor": None},
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await JournalsResource(client).list()
    assert [it.id for it in items] == [1, 2]


# ─── get(id) ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_by_id_returns_journal(settings: Settings) -> None:
    respx.get(f"{_BASE}/journals/42").respond(
        200,
        json=_journal(42, "MISC", "Opérations diverses", "miscellaneous"),
    )
    async with PennylaneClient(settings=settings) as client:
        j = await JournalsResource(client).get(42)
    assert isinstance(j, Journal)
    assert j.id == 42
    assert j.code == "MISC"
    assert j.type == "miscellaneous"


@respx.mock
async def test_get_propagates_404_as_client_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/journals/99999").respond(404, json={"error": "not found"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await JournalsResource(client).get(99999)


# ─── Résilience extra="allow" + optionnels ───────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    payload = {"items": [{**_journal(1, "BQ"), "currency": "EUR", "default_account": 512000}]}
    respx.get(f"{_BASE}/journals").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await JournalsResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["currency"] == "EUR"
    assert dumped["default_account"] == 512000


@respx.mock
async def test_optional_label_and_type_can_be_absent(settings: Settings) -> None:
    payload = {"items": [{"id": 7, "code": "X"}]}
    respx.get(f"{_BASE}/journals").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await JournalsResource(client).list()
    assert items[0].id == 7
    assert items[0].code == "X"
    assert items[0].label is None
    assert items[0].type is None


@respx.mock
async def test_empty_referential(settings: Settings) -> None:
    respx.get(f"{_BASE}/journals").respond(200, json={"items": [], "has_more": False})
    async with PennylaneClient(settings=settings) as client:
        items = await JournalsResource(client).list()
    assert items == []


# ─── Propagation erreurs ─────────────────────────────────────────────────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/journals").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await JournalsResource(client).list()
