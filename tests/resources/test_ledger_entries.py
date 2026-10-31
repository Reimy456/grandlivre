"""Tests de LedgerEntriesResource."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import (
    AccountNumberNotFoundError,
    FiscalYearNotFoundError,
)
from grandlivre.resources.ledger_entries import (
    DEFAULT_HARD_CAP,
    LedgerEntriesResource,
)

BASE = "https://app.pennylane.com/api/external/v2"


def _fy_response() -> dict:
    return {"items": [{"id": 1, "start": "2025-01-01", "finish": "2025-12-31"}]}


def _line(line_id: int, debit: str = "0", credit: str = "0", **kwargs) -> dict:
    return {
        "id": line_id,
        "label": kwargs.get("label", f"line-{line_id}"),
        "debit": debit,
        "credit": credit,
        "ledger_account": {"id": 42, "number": "411100241"},
        # Schéma 2026 : date et journal au niveau de la ligne, ledger_entry = {id}.
        "date": "2025-03-15",
        "journal": {"id": 7},
        "ledger_entry": {"id": 1000 + line_id},
    }


@respx.mock
async def test_paginate_basic_no_account_filter(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_entry_lines").respond(
        200, json={"items": [_line(1, "100.00", "0"), _line(2, "0", "50.00")]}
    )

    async with PennylaneClient(settings=settings) as client:
        page = await LedgerEntriesResource(client).paginate(exercice="2025")

    assert page.count == 2
    assert page.truncated is False
    assert page.summary is not None
    assert page.summary.total_debit == Decimal("100.00")
    assert page.summary.total_credit == Decimal("50.00")
    assert page.summary.balance == Decimal("50.00")
    assert page.filters_applied["date_from"] == "2025-01-01"
    assert page.filters_applied["date_to"] == "2025-12-31"
    assert page.filters_applied["account_number"] is None


@respx.mock
async def test_paginate_resolves_account_number(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_accounts").respond(
        200, json={"items": [{"id": 42, "number": "411100241"}]}
    )
    respx.get(f"{BASE}/ledger_entry_lines").respond(200, json={"items": [_line(1, "100", "0")]})

    async with PennylaneClient(settings=settings) as client:
        page = await LedgerEntriesResource(client).paginate(
            exercice="2025", account_number="411100241"
        )

    assert page.count == 1
    # Vérifier que le filter contient ledger_account_id=42
    le_call = next(c for c in respx.calls if "ledger_entry_lines" in str(c.request.url))
    filter_param = le_call.request.url.params["filter"]
    clauses = json.loads(filter_param)
    assert any(c.get("field") == "ledger_account_id" and c.get("value") == 42 for c in clauses)


@respx.mock
async def test_paginate_raises_if_account_unknown(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_accounts").respond(200, json={"items": []})

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(AccountNumberNotFoundError) as excinfo:
            await LedgerEntriesResource(client).paginate(exercice="2025", account_number="999999")
    assert excinfo.value.account_number == "999999"


@respx.mock
async def test_paginate_raises_if_exercice_unknown(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(
        200, json={"items": [{"id": 1, "start": "2024-01-01", "finish": "2024-12-31"}]}
    )

    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(FiscalYearNotFoundError):
            await LedgerEntriesResource(client).paginate(exercice="2099")


@respx.mock
async def test_paginate_date_override(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_entry_lines").respond(200, json={"items": []})

    async with PennylaneClient(settings=settings) as client:
        page = await LedgerEntriesResource(client).paginate(
            exercice="2025",
            date_from=date(2025, 3, 1),
            date_to="2025-06-30",
        )

    assert page.filters_applied["date_from"] == "2025-03-01"
    assert page.filters_applied["date_to"] == "2025-06-30"


@respx.mock
async def test_paginate_truncated_returns_next_cursor(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    # Renvoie 600 lignes, cap=500 → truncated=True
    items = [_line(i, "10", "0") for i in range(600)]
    respx.get(f"{BASE}/ledger_entry_lines").respond(
        200, json={"items": items, "next_cursor": "abc-123"}
    )

    async with PennylaneClient(settings=settings) as client:
        page = await LedgerEntriesResource(client).paginate(exercice="2025", cap=500)

    assert page.truncated is True
    assert page.next_cursor == "abc-123"
    assert page.summary is None
    assert page.summary_unavailable_reason == "truncated"
    assert page.count == 500


@respx.mock
async def test_transform_handles_missing_optional_fields(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_entry_lines").respond(
        200,
        json={
            "items": [
                {
                    "id": 1,
                    "debit": "0",
                    "credit": "0",
                    # Pas de ledger_entry, pas de ledger_account
                }
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        page = await LedgerEntriesResource(client).paginate(exercice="2025")

    line = page.lines[0]
    assert line.line_id == 1
    assert line.entry_id is None
    assert line.account_number == ""
    assert line.entry_date is None
    assert line.journal_id is None


@respx.mock
async def test_transform_2026_schema_journal_top_level(settings: Settings) -> None:
    """Schéma 2026 (captures réelles 2026-05-11 et 2026-07-09) : `journal` au
    niveau item, `ledger_entry` réduit à `{id}`, `date` et `label` au niveau item."""
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_entry_lines").respond(
        200,
        json={
            "items": [
                {
                    "id": 870001,
                    "label": "VTE-001 ligne 1",
                    "debit": "0.0",
                    "credit": "720.0",
                    "date": "2026-05-11",
                    "journal": {
                        "id": 26,
                        "url": "https://app.pennylane.com/.../journals/26",
                    },
                    "ledger_account": {
                        "id": 2708,
                        "number": "44574",
                    },
                    "ledger_entry": {"id": 211500},
                }
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        page = await LedgerEntriesResource(client).paginate(exercice="2025")

    line = page.lines[0]
    assert line.line_id == 870001
    assert line.entry_id == 211500
    assert line.journal_id == 26  # depuis item.journal.id
    assert line.account_number == "44574"
    assert line.line_label == "VTE-001 ligne 1"
    assert line.entry_date == "2026-05-11"  # item.date


async def test_default_hard_cap_is_500() -> None:
    assert DEFAULT_HARD_CAP == 500


@respx.mock
async def test_list_iterates_all_pages(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_entry_lines").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [_line(1, "10", "0")], "next_cursor": "c1"},
            ),
            httpx.Response(
                200,
                json={"items": [_line(2, "20", "0")], "next_cursor": None},
            ),
        ]
    )

    async with PennylaneClient(settings=settings) as client:
        lines = await LedgerEntriesResource(client).list(exercice="2025")
    assert len(lines) == 2
    assert lines[0].line_id == 1
    assert lines[1].line_id == 2


@respx.mock
async def test_transform_lit_les_lignes_lettrees(settings: Settings) -> None:
    """Forme réelle : `lettered_ledger_entry_lines` = `{ids, url}` ; `ids` vide pour une ligne
    non lettrée ; clé absente (ancienne forme) → liste vide, jamais une erreur."""
    lettree = {
        **_line(1, "100.00", "0"),
        "lettered_ledger_entry_lines": {
            "ids": [1, 7],
            "url": f"{BASE}/ledger_entry_lines/1/lettered_ledger_entry_lines",
        },
    }
    non_lettree = {
        **_line(2, "0", "100.00"),
        "lettered_ledger_entry_lines": {"ids": [], "url": "x"},
    }
    sans_cle = _line(3, "5.00", "0")
    respx.get(f"{BASE}/fiscal_years").respond(200, json=_fy_response())
    respx.get(f"{BASE}/ledger_entry_lines").respond(
        200, json={"items": [lettree, non_lettree, sans_cle]}
    )

    async with PennylaneClient(settings=settings) as client:
        page = await LedgerEntriesResource(client).paginate(exercice="2025")

    assert [line.lettered_line_ids for line in page.lines] == [
        [1, 7],
        [],
        [],
    ]
