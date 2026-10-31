"""Tests de l'utilitaire public `resolve_fiscal_year` (v0.2 A.2).

Cf. cahier des charges `a0b9a49` §3.A.2 — extraction publique
remplaçant trois duplications privées identiques (Mission F2).
"""

from __future__ import annotations

import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import FiscalYearNotFoundError
from grandlivre.fiscal_years import (
    FiscalYearInfo,
    resolve_fiscal_year,
)

_BASE = "https://app.pennylane.com/api/external/v2"


# ─── FiscalYearInfo Pydantic ─────────────────────────────────────────────────


def test_fiscal_year_info_serialises_to_dict() -> None:
    """Interface stable : id/start/finish typés str, model_dump déterministe."""
    info = FiscalYearInfo(id="42", start="2025-01-01", finish="2025-12-31")
    assert info.model_dump() == {
        "id": "42",
        "start": "2025-01-01",
        "finish": "2025-12-31",
    }


def test_fiscal_year_info_is_frozen() -> None:
    """model_config frozen=True empêche la mutation après construction."""
    info = FiscalYearInfo(id="1", start="2025-01-01", finish="2025-12-31")
    with pytest.raises((ValueError, TypeError)):
        info.id = "2"  # type: ignore[misc]


# ─── resolve_fiscal_year — chemin nominal ────────────────────────────────────


@respx.mock
async def test_resolve_returns_fiscal_year_info(settings: Settings) -> None:
    """Match du premier item dont `start` commence par `f"{exercice}-"`."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200,
        json={
            "items": [
                {"id": 1, "start": "2024-01-01", "finish": "2024-12-31"},
                {"id": 2, "start": "2025-01-01", "finish": "2025-12-31"},
            ]
        },
    )
    async with PennylaneClient(settings=settings) as client:
        info = await resolve_fiscal_year(client, "2025")
    assert isinstance(info, FiscalYearInfo)
    assert info.id == "2"
    assert info.start == "2025-01-01"
    assert info.finish == "2025-12-31"


@respx.mock
async def test_resolve_returns_first_match(settings: Settings) -> None:
    """L'itération s'arrête au premier match : ordre des items respecté."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200,
        json={
            "items": [
                {"id": "fy_a", "start": "2025-01-01", "finish": "2025-12-31"},
                {"id": "fy_b", "start": "2025-04-01", "finish": "2026-03-31"},
            ]
        },
    )
    async with PennylaneClient(settings=settings) as client:
        info = await resolve_fiscal_year(client, "2025")
    assert info.id == "fy_a"  # premier match retenu


@respx.mock
async def test_resolve_coerces_id_to_str(settings: Settings) -> None:
    """L'API peut retourner id en int — coercion explicite vers str."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200,
        json={"items": [{"id": 42, "start": "2025-01-01", "finish": "2025-12-31"}]},
    )
    async with PennylaneClient(settings=settings) as client:
        info = await resolve_fiscal_year(client, "2025")
    assert info.id == "42"
    assert isinstance(info.id, str)


# ─── resolve_fiscal_year — chemins d'erreur ──────────────────────────────────


@respx.mock
async def test_resolve_raises_when_absent(settings: Settings) -> None:
    """Exercice absent du référentiel → FiscalYearNotFoundError."""
    respx.get(f"{_BASE}/fiscal_years").respond(
        200,
        json={"items": [{"id": 1, "start": "2024-01-01", "finish": "2024-12-31"}]},
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FiscalYearNotFoundError) as excinfo:
            await resolve_fiscal_year(client, "2099")
    assert excinfo.value.exercice == "2099"


@respx.mock
async def test_resolve_raises_on_empty_referential(settings: Settings) -> None:
    """Référentiel vide → FiscalYearNotFoundError (même message)."""
    respx.get(f"{_BASE}/fiscal_years").respond(200, json={"items": []})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FiscalYearNotFoundError):
            await resolve_fiscal_year(client, "2025")
