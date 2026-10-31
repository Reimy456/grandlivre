"""Tests de LetteringResource (v0.3 B.9).

Lecture seule du lettrage comptable. Endpoint :
`GET /ledger_entry_lines/{ledger_entry_line_id}/lettered_ledger_entry_lines`.

Schéma capturé depuis `openapi_spec_raw.json` :
items `{id, debit, credit, label, categories, ledger_account,
journal, date, ledger_entry, lettered_ledger_entry_lines,
created_at, updated_at}`.

POST / DELETE `/ledger_entry_lines/lettering` (création / suppression
lettrage) **non testés** — différés P3 (action écriture, scope élargi).
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
from grandlivre.models.lettering import LetteredLedgerEntryLine
from grandlivre.resources.lettering import LetteringResource

_BASE = "https://app.pennylane.com/api/external/v2"


def _lettered_item(lid: int, debit: str = "100.00", credit: str = "0.00") -> dict:
    """Construit un item conforme au schéma capturé openapi spec."""
    return {
        "id": lid,
        "debit": debit,
        "credit": credit,
        "label": f"FAC{lid:03d}",
        "categories": [],
        "ledger_account": {"id": 1, "number": "411000"},
        "journal": {"id": 10, "code": "VTE"},
        "date": f"2026-04-{lid:02d}",
        "ledger_entry": {"id": 5000 + lid},
        "lettered_ledger_entry_lines": {"items": []},
        "created_at": f"2026-04-{lid:02d}T10:00:00Z",
        "updated_at": f"2026-04-{lid:02d}T10:00:00Z",
    }


# ─── get_lettered_lines() — cas nominal ──────────────────────────────────────


@respx.mock
async def test_get_lettered_lines_returns_typed_list(
    settings: Settings,
) -> None:
    """Cas nominal : items typés `LetteredLedgerEntryLine`, Decimal parsés."""
    line_id = 42
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").respond(
        200,
        json={
            "items": [
                _lettered_item(1, debit="100.00", credit="0.00"),
                _lettered_item(2, debit="0.00", credit="100.00"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        lines = await LetteringResource(client).get_lettered_lines(line_id)

    assert len(lines) == 2
    assert all(isinstance(li, LetteredLedgerEntryLine) for li in lines)
    assert lines[0].id == 1
    assert str(lines[0].debit) == "100.00"
    assert str(lines[1].credit) == "100.00"
    assert lines[0].label == "FAC001"


# ─── get_lettered_lines() — réponse vide ─────────────────────────────────────


@respx.mock
async def test_get_lettered_lines_empty_returns_empty_list(
    settings: Settings,
) -> None:
    """Pas de lignes lettrées → liste vide."""
    line_id = 99
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").respond(
        200, json={"items": [], "has_more": False, "next_cursor": None}
    )

    async with PennylaneClient(settings=settings) as client:
        lines = await LetteringResource(client).get_lettered_lines(line_id)
    assert lines == []


# ─── get_lettered_lines() — pagination cursor ────────────────────────────────


@respx.mock
async def test_get_lettered_lines_paginates(settings: Settings) -> None:
    """Pagination cursor : 2 pages concaténées dans la liste finale."""
    line_id = 7
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [_lettered_item(1)], "next_cursor": "c2"},
            ),
            httpx.Response(
                200,
                json={"items": [_lettered_item(2)], "next_cursor": None},
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        lines = await LetteringResource(client).get_lettered_lines(line_id)
    assert [li.id for li in lines] == [1, 2]


# ─── propagation 404 (ledger_entry_line_id inconnu) ──────────────────────────


@respx.mock
async def test_get_lettered_lines_propagates_404_unknown_id(
    settings: Settings,
) -> None:
    """`ledger_entry_line_id` inconnu → 404 → `PennylaneClientError`."""
    line_id = 9999999
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").respond(
        404, json={"status": 404, "error": "Not Found"}
    )

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError) as excinfo:
            await LetteringResource(client).get_lettered_lines(line_id)
    assert excinfo.value.status_code == 404


# ─── propagation 401 (token invalide) ────────────────────────────────────────


@respx.mock
async def test_get_lettered_lines_propagates_401_auth_error(
    settings: Settings,
) -> None:
    """Token invalide → 401 → `PennylaneAuthError`."""
    line_id = 1
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").respond(
        401, json={"status": 401, "error": "Unauthorized"}
    )

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await LetteringResource(client).get_lettered_lines(line_id)
    assert excinfo.value.status_code == 401


# ─── extra="allow" préserve champs non listés ────────────────────────────────


@respx.mock
async def test_get_lettered_lines_extra_allow_preserves_unknown_fields(
    settings: Settings,
) -> None:
    """Champ API non listé dans le modèle → préservé via `extra='allow'`."""
    line_id = 3
    item = _lettered_item(1)
    item["custom_future_field"] = "X-2027-preview"
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").respond(
        200, json={"items": [item], "next_cursor": None}
    )

    async with PennylaneClient(settings=settings) as client:
        lines = await LetteringResource(client).get_lettered_lines(line_id)
    # `extra="allow"` → champ accessible via model_dump().
    dumped = lines[0].model_dump()
    assert dumped.get("custom_future_field") == "X-2027-preview"


# ─── schéma : champs canoniques accessibles ──────────────────────────────────


@respx.mock
async def test_get_lettered_lines_canonical_fields_present(
    settings: Settings,
) -> None:
    """Vérifie l'accessibilité des champs canoniques openapi spec."""
    line_id = 11
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").respond(
        200, json={"items": [_lettered_item(50)], "next_cursor": None}
    )

    async with PennylaneClient(settings=settings) as client:
        lines = await LetteringResource(client).get_lettered_lines(line_id)
    li = lines[0]
    assert li.id == 50
    assert li.label == "FAC050"
    assert li.date == "2026-04-50"
    assert li.ledger_account == {"id": 1, "number": "411000"}
    assert li.journal == {"id": 10, "code": "VTE"}
    assert li.ledger_entry == {"id": 5050}
    assert li.created_at is not None
    assert li.updated_at is not None


# ─── iterate_lettered_lines() — itérateur asynchrone ─────────────────────────


@respx.mock
async def test_iterate_lettered_lines_yields(settings: Settings) -> None:
    """`iterate_lettered_lines()` itère sans matérialiser."""
    line_id = 5
    respx.get(f"{_BASE}/ledger_entry_lines/{line_id}/lettered_ledger_entry_lines").respond(
        200,
        json={"items": [_lettered_item(1), _lettered_item(2)], "next_cursor": None},
    )
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for li in LetteringResource(client).iterate_lettered_lines(line_id):
            ids.append(li.id)
    assert ids == [1, 2]
