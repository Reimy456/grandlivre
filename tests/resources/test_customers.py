"""Tests de CustomersResource (v0.3 B.12).

Schéma `/customers` : `oneOf` à 2 variantes empiriquement company /
individual (titles `"Company customer"` / `"Individual customer"`).
Pattern errata `cb612a6d` `customers_response_with_or_without_categories`
(cf. `docs/errata_openapi_pennylane.md` §2, 3 occurrences). La lib
mappe les deux variantes sur un modèle unique `Customer` avec champs
union (optionnels), `extra="allow"`.

Schéma `/customers/{id}` identique au schéma item liste (vérifié via
spec OpenAPI).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError, PennylaneClientError
from grandlivre.models.customers import Customer
from grandlivre.resources.customers import CustomersResource

_BASE = "https://app.pennylane.com/api/external/v2"


def _company_customer(cid: int, name: str = "ACME SAS") -> dict:
    """Payload variante `Company customer` (`oneOf[0]`)."""
    return {
        "id": cid,
        "name": name,
        "customer_type": "company",
        "vat_number": "FR12345678901",
        "reg_no": "123456789",
        "billing_iban": "FR1420041010050500013M02606",
        "payment_conditions": "30_days",
        "recipient": "John Doe",
        "phone": "+33612345678",
        "reference": "REF-1234",
        "notes": None,
        "ledger_account": {"id": 411, "number": "411000"},
        "emails": ["billing@acme.example"],
        "billing_address": {"city": "Paris"},
        "delivery_address": {"city": "Paris"},
        "created_at": "2026-01-15T09:30:00Z",
        "updated_at": "2026-04-20T14:00:00Z",
        "external_reference": "ERP-42",
        "billing_language": "fr",
        "mandates": {},
        "pro_account_mandates": {},
        "contacts": {},
    }


def _individual_customer(cid: int, first: str = "Alice", last: str = "Martin") -> dict:
    """Payload variante `Individual customer` (`oneOf[1]`)."""
    return {
        "id": cid,
        "name": f"{first} {last}",
        "customer_type": "individual",
        "first_name": first,
        "last_name": last,
        "billing_iban": "FR7630006000011234567890189",
        "payment_conditions": "upon_receipt",
        "recipient": f"{first} {last}",
        "phone": "+33687654321",
        "reference": None,
        "notes": "Particulier",
        "ledger_account": {"id": 411, "number": "411000"},
        "emails": [f"{first.lower()}@example.test"],
        "billing_address": {"city": "Lyon"},
        "delivery_address": {"city": "Lyon"},
        "created_at": "2026-02-01T10:00:00Z",
        "updated_at": "2026-02-01T10:00:00Z",
        "external_reference": None,
        "billing_language": "fr",
        "mandates": {},
        "pro_account_mandates": {},
        "contacts": {},
    }


# ─── Chemin nominal ──────────────────────────────────────────────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    respx.get(f"{_BASE}/customers").respond(
        200,
        json={
            "items": [
                _company_customer(1),
                _individual_customer(2),
                _company_customer(3, "Beta Corp"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await CustomersResource(client).list()
    assert len(items) == 3
    assert all(isinstance(it, Customer) for it in items)
    assert items[0].id == 1
    assert items[0].name == "ACME SAS"
    assert items[2].name == "Beta Corp"


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    respx.get(f"{_BASE}/customers").respond(
        200,
        json={"items": [_company_customer(1), _individual_customer(2)]},
    )
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for c in CustomersResource(client).iterate():
            ids.append(c.id)
    assert ids == [1, 2]


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{_BASE}/customers").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [_company_customer(1)], "next_cursor": "c2"},
            ),
            httpx.Response(
                200,
                json={"items": [_individual_customer(2)], "next_cursor": None},
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await CustomersResource(client).list()
    assert [it.id for it in items] == [1, 2]


# ─── get(id) ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_by_id_returns_customer(settings: Settings) -> None:
    respx.get(f"{_BASE}/customers/42").respond(200, json=_company_customer(42, "Gamma"))
    async with PennylaneClient(settings=settings) as client:
        c = await CustomersResource(client).get(42)
    assert isinstance(c, Customer)
    assert c.id == 42
    assert c.name == "Gamma"
    assert c.customer_type == "company"
    assert c.vat_number == "FR12345678901"


@respx.mock
async def test_get_propagates_404_as_client_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/customers/99999").respond(404, json={"error": "not found"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await CustomersResource(client).get(99999)


# ─── Variantes oneOf : Company vs Individual ─────────────────────────────────


@respx.mock
async def test_variant_company_customer_fields_populated(settings: Settings) -> None:
    """Variante `Company customer` (`oneOf[0]`) : `vat_number` + `reg_no`
    populés, `first_name`/`last_name` absents (mappés `None`)."""
    respx.get(f"{_BASE}/customers").respond(
        200, json={"items": [_company_customer(10, "Company Ltd")]}
    )
    async with PennylaneClient(settings=settings) as client:
        items = await CustomersResource(client).list()
    c = items[0]
    assert c.customer_type == "company"
    assert c.vat_number == "FR12345678901"
    assert c.reg_no == "123456789"
    assert c.first_name is None
    assert c.last_name is None


@respx.mock
async def test_variant_individual_customer_fields_populated(settings: Settings) -> None:
    """Variante `Individual customer` (`oneOf[1]`) : `first_name` +
    `last_name` populés, `vat_number`/`reg_no` absents (mappés `None`)."""
    respx.get(f"{_BASE}/customers").respond(
        200, json={"items": [_individual_customer(20, "Bob", "Dupont")]}
    )
    async with PennylaneClient(settings=settings) as client:
        items = await CustomersResource(client).list()
    c = items[0]
    assert c.customer_type == "individual"
    assert c.first_name == "Bob"
    assert c.last_name == "Dupont"
    assert c.vat_number is None
    assert c.reg_no is None


# ─── Résilience extra="allow" ────────────────────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    """`extra="allow"` préserve les champs API non listés (ex. `categories`
    populées sur certains endpoints, cf. nom errata `cb612a6d`)."""
    payload = {
        "items": [
            {
                **_company_customer(1),
                "categories": [{"id": 5, "label": "Premium"}],
                "internal_flag": True,
            }
        ]
    }
    respx.get(f"{_BASE}/customers").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await CustomersResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["categories"] == [{"id": 5, "label": "Premium"}]
    assert dumped["internal_flag"] is True


@respx.mock
async def test_empty_referential(settings: Settings) -> None:
    respx.get(f"{_BASE}/customers").respond(200, json={"items": [], "has_more": False})
    async with PennylaneClient(settings=settings) as client:
        items = await CustomersResource(client).list()
    assert items == []


# ─── Propagation erreurs ─────────────────────────────────────────────────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/customers").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await CustomersResource(client).list()
