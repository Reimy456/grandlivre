"""Tests de FiscalYearsResource (v0.3 B.2).

Schéma `/fiscal_years` capturé empiriquement 2026-05-12 SID `6afe82d0`
sur dossier test : 6 exercices, statut « open » sur les 4 derniers et
« closed » sur les 2 antérieurs.

Tests respx mockés — zéro appel réseau réel.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError
from grandlivre.models.fiscal_years import FiscalYear
from grandlivre.resources.fiscal_years import FiscalYearsResource

_BASE = "https://app.pennylane.com/api/external/v2"


def _fy(fy_id: int, year: int, status: str = "open") -> dict:
    return {
        "id": fy_id,
        "start": f"{year}-01-01",
        "finish": f"{year}-12-31",
        "status": status,
        "created_at": f"{year - 1}-12-15T10:00:00Z",
        "updated_at": f"{year}-06-01T12:00:00Z",
    }


# ─── Chemin nominal ──────────────────────────────────────────────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    """Une page → list() retourne tous les FiscalYear typés."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200,
        json={
            "items": [
                _fy(1, 2025),
                _fy(2, 2024),
                _fy(3, 2023, status="closed"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await FiscalYearsResource(client).list()
    assert len(items) == 3
    assert all(isinstance(it, FiscalYear) for it in items)
    assert items[0].id == 1
    assert items[0].start == "2025-01-01"
    assert items[0].finish == "2025-12-31"
    assert items[0].status == "open"
    assert items[2].status == "closed"


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    """Pagination cursor sur plusieurs pages : tous les items récupérés."""
    respx.get(f"{_BASE}/fiscal_years").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [_fy(1, 2025), _fy(2, 2024)],
                    "next_cursor": "page2",
                },
            ),
            httpx.Response(
                200,
                json={
                    "items": [_fy(3, 2023, status="closed")],
                    "next_cursor": None,
                },
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await FiscalYearsResource(client).list()
    assert len(items) == 3
    assert [it.id for it in items] == [1, 2, 3]


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    """`iterate` yield chaque exercice sans matérialiser la liste."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200,
        json={
            "items": [_fy(1, 2025), _fy(2, 2024)],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for fy in FiscalYearsResource(client).iterate():
            ids.append(fy.id)
    assert ids == [1, 2]


@respx.mock
async def test_status_open_closed_preserved(settings: Settings) -> None:
    """Le champ `status` (« open » / « closed ») est typé `str` et préservé."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200,
        json={
            "items": [
                _fy(1, 2025, status="open"),
                _fy(2, 2024, status="closed"),
            ]
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await FiscalYearsResource(client).list()
    assert items[0].status == "open"
    assert items[1].status == "closed"


# ─── Résilience extra="allow" ────────────────────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    """Nouveau champ inattendu → conservé (extra='allow')."""
    payload = {
        "items": [
            {
                **_fy(1, 2025),
                "fiscal_period_type": "annual",  # champ non listé
            }
        ]
    }
    respx.get(f"{_BASE}/fiscal_years").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await FiscalYearsResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["fiscal_period_type"] == "annual"


@respx.mock
async def test_optional_timestamps_can_be_absent(settings: Settings) -> None:
    """`created_at` / `updated_at` / `status` absents → None (champs Optional)."""
    payload = {"items": [{"id": 42, "start": "2025-01-01", "finish": "2025-12-31"}]}
    respx.get(f"{_BASE}/fiscal_years").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await FiscalYearsResource(client).list()
    assert items[0].id == 42
    assert items[0].status is None
    assert items[0].created_at is None
    assert items[0].updated_at is None


@respx.mock
async def test_empty_referential(settings: Settings) -> None:
    """Référentiel vide → liste vide (pas d'erreur)."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200, json={"items": [], "has_more": False, "next_cursor": None}
    )
    async with PennylaneClient(settings=settings) as client:
        items = await FiscalYearsResource(client).list()
    assert items == []


# ─── Propagation erreurs ─────────────────────────────────────────────────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    """Token invalide → PennylaneAuthError remonte (pas retried)."""
    respx.get(f"{_BASE}/fiscal_years").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await FiscalYearsResource(client).list()
