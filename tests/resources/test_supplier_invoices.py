"""Tests de SupplierInvoicesResource."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.models.supplier_invoices import LedgerEntryRef
from grandlivre.resources.supplier_invoices import SupplierInvoicesResource

BASE = "https://app.pennylane.com/api/external/v2"


@respx.mock
async def test_list_basic(settings: Settings) -> None:
    respx.get(f"{BASE}/supplier_invoices").respond(
        200,
        json={
            "items": [
                {
                    "id": 1,
                    "invoice_number": "INV-001",
                    "date": "2025-03-15",
                    "amount": "1200.50",
                    "currency": "EUR",
                    "tax_amount": "200.00",
                },
                {
                    "id": 2,
                    "invoice_number": "INV-002",
                    "date": "2025-04-01",
                    "amount": "300.00",
                    "currency": "EUR",
                },
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        invoices = await SupplierInvoicesResource(client).list()

    assert len(invoices) == 2
    assert invoices[0].id == 1
    assert invoices[0].amount == Decimal("1200.50")
    assert invoices[0].tax_amount == Decimal("200.00")
    assert invoices[1].tax_amount is None


@respx.mock
async def test_list_with_date_filters(settings: Settings) -> None:
    """Les filtres partent dans le SEUL paramètre que l'API applique : ``filter`` (liste JSON
    de clauses) — jamais en clair (``date_from=``, ``status=`` étaient ignorés en silence)."""
    route = respx.get(f"{BASE}/supplier_invoices").respond(200, json={"items": []})

    async with PennylaneClient(settings=settings) as client:
        await SupplierInvoicesResource(client).list(
            date_from=date(2025, 1, 1),
            date_to="2025-12-31",
            supplier_id="sup-42",
            payment_status="fully_paid",
        )

    params = route.calls.last.request.url.params
    assert json.loads(params["filter"]) == [
        {"field": "date", "operator": "gteq", "value": "2025-01-01"},
        {"field": "date", "operator": "lteq", "value": "2025-12-31"},
        {"field": "supplier_id", "operator": "eq", "value": "sup-42"},
        {"field": "payment_status", "operator": "eq", "value": "fully_paid"},
    ]
    assert params["limit"] == "100"
    assert set(params.keys()) == {"filter", "limit"}


@respx.mock
async def test_list_sans_filtre_n_envoie_que_limit(settings: Settings) -> None:
    route = respx.get(f"{BASE}/supplier_invoices").respond(200, json={"items": []})
    async with PennylaneClient(settings=settings) as client:
        await SupplierInvoicesResource(client).list()
    assert set(route.calls.last.request.url.params.keys()) == {"limit"}


@respx.mock
async def test_paginate_garde_le_filtre_sur_la_page_suivante(settings: Settings) -> None:
    route = respx.get(f"{BASE}/supplier_invoices").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1, "amount": "1"}], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [{"id": 2, "amount": "1"}], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        page = await SupplierInvoicesResource(client).paginate(date_from="2025-01-01", cap=10)
    assert page.count == 2
    deuxieme = route.calls[1].request.url.params
    assert deuxieme["cursor"] == "c1"
    assert json.loads(deuxieme["filter"]) == [
        {"field": "date", "operator": "gteq", "value": "2025-01-01"}
    ]


@respx.mock
async def test_paginate_truncated(settings: Settings) -> None:
    respx.get(f"{BASE}/supplier_invoices").respond(
        200,
        json={
            "items": [{"id": i, "amount": "1"} for i in range(10)],
            "next_cursor": "c1",
        },
    )

    async with PennylaneClient(settings=settings) as client:
        page = await SupplierInvoicesResource(client).paginate(cap=5)

    assert page.truncated is True
    assert page.next_cursor == "c1"
    assert page.count == 5


@respx.mock
async def test_iterate_multiple_pages(settings: Settings) -> None:
    respx.get(f"{BASE}/supplier_invoices").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1, "amount": "10"}], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [{"id": 2, "amount": "20"}], "next_cursor": None}),
        ]
    )

    async with PennylaneClient(settings=settings) as client:
        out = []
        async for inv in SupplierInvoicesResource(client).iterate():
            out.append(inv.id)
    assert out == [1, 2]


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    """`extra='allow'` : les champs API non listés explicitement sont préservés."""
    respx.get(f"{BASE}/supplier_invoices").respond(
        200,
        json={
            "items": [
                {
                    "id": 1,
                    "invoice_number": "X",
                    "amount": "100",
                    "custom_field_not_in_model": "preserved-value",
                }
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        invoices = await SupplierInvoicesResource(client).list()

    dumped = invoices[0].model_dump()
    assert dumped["custom_field_not_in_model"] == "preserved-value"


@respx.mock
async def test_amount_empty_string_becomes_none(settings: Settings) -> None:
    respx.get(f"{BASE}/supplier_invoices").respond(
        200, json={"items": [{"id": 1, "amount": "", "tax_amount": None}]}
    )

    async with PennylaneClient(settings=settings) as client:
        invoices = await SupplierInvoicesResource(client).list()
    assert invoices[0].amount is None
    assert invoices[0].tax_amount is None


@respx.mock
async def test_echeance_lue_sous_deadline(settings: Settings) -> None:
    """L'échéance arrive sous `deadline` (spec, et constaté sur le dossier test le 21/10/2026 :
    26 factures, toutes en `deadline`) ; le modèle ne sert plus un `due_date` toujours vide."""
    respx.get(f"{BASE}/supplier_invoices").respond(
        200, json={"items": [{"id": 1, "date": "2025-12-15", "deadline": "2026-01-14"}]}
    )

    async with PennylaneClient(settings=settings) as client:
        invoices = await SupplierInvoicesResource(client).list()
    assert invoices[0].deadline == "2026-01-14"
    assert "due_date" not in invoices[0].model_dump()


@respx.mock
async def test_ledger_entry_type_et_conserve(settings: Settings) -> None:
    """`ledger_entry: {id}` (spec `SupplierInvoices__Response`) est typé : clé de la
    jointure ligne d'écriture ↔ facture (lot 0 B0.2)."""
    respx.get(f"{BASE}/supplier_invoices").respond(
        200,
        json={
            "items": [
                {"id": 1, "amount": "10", "ledger_entry": {"id": 42003}},
                {"id": 2, "amount": "5"},
            ]
        },
    )
    async with PennylaneClient(settings=settings) as client:
        invoices = await SupplierInvoicesResource(client).list()
    assert isinstance(invoices[0].ledger_entry, LedgerEntryRef)
    assert invoices[0].ledger_entry.id == 42003
    assert invoices[1].ledger_entry is None


@respx.mock
async def test_invoice_lines_paginees_avec_imputation_dates(settings: Settings) -> None:
    page_1 = {
        "has_more": True,
        "next_cursor": "c1",
        "items": [
            {
                "id": 70,
                "label": "Abonnement annuel",
                "amount": "1200.00",
                "vat_rate": "FR_200",
                "currency_amount_before_tax": "1000.00",
                "currency_tax": "200.00",
                "imputation_dates": {"start_date": "2025-03-01", "end_date": "2026-02-28"},
            }
        ],
    }
    page_2 = {
        "has_more": False,
        "next_cursor": None,
        "items": [
            {"id": 71, "label": "Frais de port", "amount": "10.00", "imputation_dates": None}
        ],
    }
    route = respx.get(f"{BASE}/supplier_invoices/7/invoice_lines").mock(
        side_effect=[httpx.Response(200, json=page_1), httpx.Response(200, json=page_2)]
    )
    async with PennylaneClient(settings=settings) as client:
        lignes = await SupplierInvoicesResource(client).invoice_lines(7)
    assert [ligne.id for ligne in lignes] == [70, 71]
    assert lignes[0].imputation_dates is not None
    assert lignes[0].imputation_dates.start_date == "2025-03-01"
    assert lignes[0].amount == Decimal("1200.00")
    assert lignes[1].imputation_dates is None
    assert route.calls[1].request.url.params["cursor"] == "c1"
