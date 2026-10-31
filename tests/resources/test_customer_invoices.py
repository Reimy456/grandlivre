"""Tests de CustomerInvoicesResource.

Symétrie avec `test_supplier_invoices.py` (cf. cahier des charges §4.2 B.10).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.resources.customer_invoices import CustomerInvoicesResource

BASE = "https://app.pennylane.com/api/external/v2"


@respx.mock
async def test_list_basic(settings: Settings) -> None:
    respx.get(f"{BASE}/customer_invoices").respond(
        200,
        json={
            "items": [
                {
                    "id": 1,
                    "invoice_number": "CUST-001",
                    "date": "2025-03-15",
                    "amount": "1200.50",
                    "currency": "EUR",
                    "tax": "200.00",
                },
                {
                    "id": 2,
                    "invoice_number": "CUST-002",
                    "date": "2025-04-01",
                    "amount": "300.00",
                    "currency": "EUR",
                },
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        invoices = await CustomerInvoicesResource(client).list()

    assert len(invoices) == 2
    assert invoices[0].id == 1
    assert invoices[0].amount == Decimal("1200.50")
    assert invoices[0].tax == Decimal("200.00")
    assert invoices[1].tax is None


@respx.mock
async def test_list_with_date_filters(settings: Settings) -> None:
    """Les filtres partent dans le SEUL paramètre que l'API applique : ``filter`` (liste JSON
    de clauses) ; ``draft`` / ``credit_note`` sont les deux « statuts » filtrables (booléens,
    forme ``"true"`` de la spec) — l'API n'a pas de filtre ``status``."""
    route = respx.get(f"{BASE}/customer_invoices").respond(200, json={"items": []})

    async with PennylaneClient(settings=settings) as client:
        await CustomerInvoicesResource(client).list(
            date_from=date(2025, 1, 1),
            date_to="2025-12-31",
            customer_id="cust-42",
            draft=True,
            credit_note=False,
        )

    params = route.calls.last.request.url.params
    assert json.loads(params["filter"]) == [
        {"field": "date", "operator": "gteq", "value": "2025-01-01"},
        {"field": "date", "operator": "lteq", "value": "2025-12-31"},
        {"field": "customer_id", "operator": "eq", "value": "cust-42"},
        {"field": "draft", "operator": "eq", "value": "true"},
        {"field": "credit_note", "operator": "eq", "value": "false"},
    ]
    assert set(params.keys()) == {"filter", "limit"}


@respx.mock
async def test_paginate_filtre_par_numero_de_facture(settings: Settings) -> None:
    """Recherche exacte d'un numéro (filtre `invoice_number eq`, spec) : une requête, quelle que
    soit la taille du dossier — ce qu'un contrôle d'unicité exige au-delà du cap de lecture."""
    route = respx.get(f"{BASE}/customer_invoices").respond(200, json={"items": []})
    async with PennylaneClient(settings=settings) as client:
        page = await CustomerInvoicesResource(client).paginate(invoice_number="F-2026-284")

    assert page.invoices == [] and page.truncated is False
    params = route.calls.last.request.url.params
    assert json.loads(params["filter"]) == [
        {"field": "invoice_number", "operator": "eq", "value": "F-2026-284"},
    ]
    assert set(params.keys()) == {"filter", "limit"}


@respx.mock
async def test_list_sans_filtre_n_envoie_que_limit(settings: Settings) -> None:
    route = respx.get(f"{BASE}/customer_invoices").respond(200, json={"items": []})
    async with PennylaneClient(settings=settings) as client:
        await CustomerInvoicesResource(client).list()
    assert set(route.calls.last.request.url.params.keys()) == {"limit"}


@respx.mock
async def test_paginate_truncated(settings: Settings) -> None:
    respx.get(f"{BASE}/customer_invoices").respond(
        200,
        json={
            "items": [{"id": i, "amount": "1"} for i in range(10)],
            "next_cursor": "c1",
        },
    )

    async with PennylaneClient(settings=settings) as client:
        page = await CustomerInvoicesResource(client).paginate(cap=5)

    assert page.truncated is True
    assert page.next_cursor == "c1"
    assert page.count == 5


@respx.mock
async def test_iterate_multiple_pages(settings: Settings) -> None:
    respx.get(f"{BASE}/customer_invoices").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1, "amount": "10"}], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [{"id": 2, "amount": "20"}], "next_cursor": None}),
        ]
    )

    async with PennylaneClient(settings=settings) as client:
        out = []
        async for inv in CustomerInvoicesResource(client).iterate():
            out.append(inv.id)
    assert out == [1, 2]


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    """`extra='allow'` : les champs API non listés explicitement sont préservés."""
    respx.get(f"{BASE}/customer_invoices").respond(
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
        invoices = await CustomerInvoicesResource(client).list()

    dumped = invoices[0].model_dump()
    assert dumped["custom_field_not_in_model"] == "preserved-value"


@respx.mock
async def test_amount_empty_string_becomes_none(settings: Settings) -> None:
    respx.get(f"{BASE}/customer_invoices").respond(
        200, json={"items": [{"id": 1, "amount": "", "tax": None}]}
    )

    async with PennylaneClient(settings=settings) as client:
        invoices = await CustomerInvoicesResource(client).list()
    assert invoices[0].amount is None
    assert invoices[0].tax is None
