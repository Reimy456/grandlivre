"""Tests de TrialBalanceResource."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import FiscalYearNotFoundError
from grandlivre.resources.trial_balance import TrialBalanceResource

BASE = "https://app.pennylane.com/api/external/v2"


@respx.mock
async def test_get_resolves_fiscal_year_then_iterates(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(
        200,
        json={
            "items": [
                {"id": 1, "start": "2025-01-01", "finish": "2025-12-31"},
                {"id": 2, "start": "2024-01-01", "finish": "2024-12-31"},
            ]
        },
    )
    respx.get(f"{BASE}/trial_balance").respond(
        200,
        json={
            "items": [
                {
                    "number": "512",
                    "label": "Banque",
                    "formatted_number": "51200000",
                    "debits": "1200.50",
                    "credits": "100.00",
                },
                {
                    "number": "401",
                    "label": "Fournisseurs",
                    "formatted_number": "40100000",
                    "debits": "0",
                    "credits": "2500.00",
                },
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        tb = await TrialBalanceResource(client).get(exercice="2025")

    assert tb.exercice == "2025"
    assert tb.period_start == "2025-01-01"
    assert tb.period_end == "2025-12-31"
    assert len(tb.accounts) == 2
    assert tb.accounts[0].number == "512"
    assert tb.accounts[0].debits == Decimal("1200.50")
    assert tb.accounts[0].balance == Decimal("1100.50")
    assert tb.accounts[1].balance == Decimal("-2500.00")


@respx.mock
async def test_get_raises_when_fiscal_year_absent(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(
        200, json={"items": [{"id": 99, "start": "2024-01-01", "finish": "2024-12-31"}]}
    )

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FiscalYearNotFoundError) as excinfo:
            await TrialBalanceResource(client).get(exercice="2099")
    assert excinfo.value.exercice == "2099"


@respx.mock
async def test_get_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(
        200, json={"items": [{"id": 1, "start": "2025-01-01", "finish": "2025-12-31"}]}
    )
    respx.get(f"{BASE}/trial_balance").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [
                        {"number": "512", "label": "L", "debits": "10", "credits": "0"},
                    ],
                    "next_cursor": "c1",
                },
            ),
            httpx.Response(
                200,
                json={
                    "items": [
                        {"number": "401", "label": "L", "debits": "0", "credits": "20"},
                    ],
                    "next_cursor": None,
                },
            ),
        ]
    )

    async with PennylaneClient(settings=settings) as client:
        tb = await TrialBalanceResource(client).get(exercice="2025")

    assert len(tb.accounts) == 2


@respx.mock
async def test_iterate_yields_each_account(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(
        200, json={"items": [{"id": 1, "start": "2025-01-01", "finish": "2025-12-31"}]}
    )
    respx.get(f"{BASE}/trial_balance").respond(
        200,
        json={
            "items": [
                {"number": "1", "label": "A", "debits": "1", "credits": "0"},
                {"number": "2", "label": "B", "debits": "0", "credits": "1"},
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        seen = []
        async for acc in TrialBalanceResource(client).iterate(exercice="2025"):
            seen.append(acc.number)
    assert seen == ["1", "2"]
