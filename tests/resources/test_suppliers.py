"""Tests de SuppliersResource (v0.3 B.11).

Schéma `/suppliers` capturé depuis `openapi_spec_raw.json`
(2026-05-12) :
    {items: [{id, name, establishment_no?, reg_no?, vat_number,
              ledger_account?, emails, iban, postal_address,
              supplier_payment_method?, supplier_due_date_delay?,
              supplier_due_date_rule?, external_reference,
              created_at, updated_at}],
     has_more, next_cursor}

Schéma `/suppliers/{id}` identique au schéma item de la liste (vérifié spec).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError, PennylaneClientError
from grandlivre.models.suppliers import Supplier
from grandlivre.resources.suppliers import SuppliersResource

_BASE = "https://app.pennylane.com/api/external/v2"


def _supplier(
    sid: int,
    name: str = "Acme Corp",
    vat_number: str = "FR32123456789",
    siret: str | None = "82762938500014",
    siren: str | None = "827629385",
) -> dict:
    return {
        "id": sid,
        "name": name,
        "establishment_no": siret,
        "reg_no": siren,
        "vat_number": vat_number,
        "ledger_account": {"id": 1001, "number": "401000", "label": "Fournisseurs"},
        "emails": ["billing@acme.example"],
        "iban": "FR7630006000011234567890189",
        "postal_address": {"street": "1 rue de la Paix", "city": "Paris", "country": "FR"},
        "supplier_payment_method": "transfer",
        "supplier_due_date_delay": 30,
        "supplier_due_date_rule": "end_of_month",
        "external_reference": f"EXT-{sid}",
        "created_at": "2026-01-15T10:00:00Z",
        "updated_at": "2026-04-20T14:30:00Z",
    }


# ─── Chemin nominal ──────────────────────────────────────────────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    respx.get(f"{_BASE}/suppliers").respond(
        200,
        json={
            "items": [
                _supplier(1, "Acme Corp"),
                _supplier(2, "Beta SAS", vat_number="FR40987654321"),
                _supplier(3, "Gamma Ltd", siret=None, siren=None),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await SuppliersResource(client).list()
    assert len(items) == 3
    assert all(isinstance(s, Supplier) for s in items)
    assert items[0].id == 1
    assert items[0].name == "Acme Corp"
    assert items[0].vat_number == "FR32123456789"
    assert items[0].establishment_no == "82762938500014"
    assert items[2].establishment_no is None
    assert items[2].reg_no is None


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    respx.get(f"{_BASE}/suppliers").respond(
        200,
        json={"items": [_supplier(1, "Acme"), _supplier(2, "Beta")]},
    )
    async with PennylaneClient(settings=settings) as client:
        names = []
        async for s in SuppliersResource(client).iterate():
            names.append(s.name)
    assert names == ["Acme", "Beta"]


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{_BASE}/suppliers").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [_supplier(1, "Acme")], "next_cursor": "c2"},
            ),
            httpx.Response(
                200,
                json={"items": [_supplier(2, "Beta")], "next_cursor": "c3"},
            ),
            httpx.Response(
                200,
                json={"items": [_supplier(3, "Gamma")], "next_cursor": None},
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await SuppliersResource(client).list()
    assert [s.id for s in items] == [1, 2, 3]
    assert [s.name for s in items] == ["Acme", "Beta", "Gamma"]


# ─── get(id) ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_by_id_returns_supplier(settings: Settings) -> None:
    respx.get(f"{_BASE}/suppliers/42").respond(
        200,
        json=_supplier(42, "Detail Corp"),
    )
    async with PennylaneClient(settings=settings) as client:
        s = await SuppliersResource(client).get(42)
    assert isinstance(s, Supplier)
    assert s.id == 42
    assert s.name == "Detail Corp"
    assert s.iban == "FR7630006000011234567890189"
    assert s.external_reference == "EXT-42"


@respx.mock
async def test_get_propagates_404_as_client_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/suppliers/99999").respond(404, json={"error": "not found"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await SuppliersResource(client).get(99999)


# ─── Résilience extra="allow" + payload minimal ──────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    payload = {
        "items": [
            {
                **_supplier(1, "Acme"),
                "custom_tag": "vip",
                "internal_score": 87,
            }
        ]
    }
    respx.get(f"{_BASE}/suppliers").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await SuppliersResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["custom_tag"] == "vip"
    assert dumped["internal_score"] == 87


@respx.mock
async def test_minimal_payload_id_and_name_only(settings: Settings) -> None:
    """Cas minimal : un fournisseur avec seulement id + name (autres champs
    optionnels absents). `extra="allow"` + champs optionnels doivent permettre
    la validation sans erreur."""
    payload = {"items": [{"id": 7, "name": "Minimal Co"}]}
    respx.get(f"{_BASE}/suppliers").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await SuppliersResource(client).list()
    assert items[0].id == 7
    assert items[0].name == "Minimal Co"
    assert items[0].vat_number is None
    assert items[0].ledger_account is None
    assert items[0].iban is None
    assert items[0].emails is None


# ─── Propagation erreurs ─────────────────────────────────────────────────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/suppliers").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await SuppliersResource(client).list()
