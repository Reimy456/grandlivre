"""Tests de TransactionsResource (v0.3 B.7).

**Bloqueur scope sandbox** : Company Token actuel n'a pas
`transactions:readonly` (HTTP 403 capturé empiriquement 2026-05-12
SID `6afe82d0`). Tests respx mockent les réponses — pas de smoke
sandbox tant que scope absent.

Schéma `Transaction` sourcé `openapi_spec_raw.json` zone
`/api/external/v2/transactions` (21 champs canoniques).
"""

from __future__ import annotations

import json
from datetime import date

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError, PennylaneClientError
from grandlivre.models.transactions import MatchedInvoice, Transaction
from grandlivre.resources.transactions import (
    DEFAULT_HARD_CAP,
    TransactionsResource,
)

_BASE = "https://app.pennylane.com/api/external/v2"


def _transaction(tid: int, amount: str = "100.00", label: str = "Test op") -> dict:
    """Fixture synthétique cohérente schéma OpenAPI."""
    return {
        "id": tid,
        "label": label,
        "attachment_required": False,
        "date": "2026-05-11",
        "outstanding_balance": "0.00",
        "created_at": "2026-05-11T10:00:00Z",
        "updated_at": "2026-05-11T10:00:00Z",
        "archived_at": None,
        "currency": "EUR",
        "currency_amount": amount,
        "amount": amount,
        "currency_fee": None,
        "fee": None,
        "journal": {"id": 1000, "code": "BQ"},
        "bank_account": {"id": 2000, "label": "Compte courant"},
        "pro_account_expense": None,
        "customer": None,
        "supplier": None,
        "categories": [],
        "matched_invoices": {},
        "interbank_code": None,
    }


# ─── Bloqueur scope sandbox (HTTP 403) — cas reproductible empiriquement ─────


@respx.mock
async def test_propagates_403_when_scope_missing(settings: Settings) -> None:
    """Cas empirique sandbox actuel : Company Token sans scope
    `transactions:readonly` → HTTP 403 (`PennylaneAuthError`)."""
    respx.get(f"{_BASE}/transactions").respond(
        403,
        json={
            "status": 403,
            "error": (
                'Access to this resource requires scope "transactions:all transactions:readonly".'
            ),
        },
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await TransactionsResource(client).list()
    assert excinfo.value.status_code == 403


# ─── Chemin nominal (post-renouvellement token avec scope) ───────────────────


@respx.mock
async def test_list_returns_all_items(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions").respond(
        200,
        json={
            "items": [
                _transaction(1, "100.00", "Virement client"),
                _transaction(2, "-50.00", "Prélèvement EDF"),
                _transaction(3, "1200.50", "Encaissement chèque"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
    )
    async with PennylaneClient(settings=settings) as client:
        items = await TransactionsResource(client).list()
    assert len(items) == 3
    assert all(isinstance(it, Transaction) for it in items)
    assert items[0].id == 1
    assert items[0].label == "Virement client"
    assert items[0].amount == "100.00"
    assert items[0].currency == "EUR"
    assert items[1].amount == "-50.00"
    assert items[2].amount == "1200.50"


@respx.mock
async def test_iterate_yields_one_by_one(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions").respond(
        200, json={"items": [_transaction(1), _transaction(2)]}
    )
    async with PennylaneClient(settings=settings) as client:
        ids = []
        async for t in TransactionsResource(client).iterate():
            ids.append(t.id)
    assert ids == [1, 2]


@respx.mock
async def test_list_paginates_multiple_pages(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions").mock(
        side_effect=[
            httpx.Response(200, json={"items": [_transaction(1)], "next_cursor": "c2"}),
            httpx.Response(200, json={"items": [_transaction(2)], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = await TransactionsResource(client).list()
    assert [it.id for it in items] == [1, 2]


# ─── paginate avec cap dur ───────────────────────────────────────────────────


@respx.mock
async def test_paginate_truncated_returns_next_cursor(
    settings: Settings,
) -> None:
    items = [_transaction(i) for i in range(600)]
    respx.get(f"{_BASE}/transactions").respond(200, json={"items": items, "next_cursor": "abc"})
    async with PennylaneClient(settings=settings) as client:
        page, truncated, nc = await TransactionsResource(client).paginate(cap=500)
    assert truncated is True
    assert nc == "abc"
    assert len(page) == 500


async def test_default_hard_cap_is_500() -> None:
    assert DEFAULT_HARD_CAP == 500


# ─── paginate : filtres date et compte bancaire (paramètre `filter` JSON) ────


@respx.mock
async def test_paginate_filtres_partent_dans_le_parametre_filter(settings: Settings) -> None:
    route = respx.get(f"{_BASE}/transactions").respond(
        200, json={"items": [_transaction(1)], "next_cursor": None}
    )
    async with PennylaneClient(settings=settings) as client:
        page, truncated, nc = await TransactionsResource(client).paginate(
            date_from=date(2025, 1, 1), date_to="2025-12-31", bank_account_id=1001
        )
    assert [t.id for t in page] == [1] and truncated is False and nc is None
    params = route.calls[0].request.url.params
    assert json.loads(params["filter"]) == [
        {"field": "date", "operator": "gteq", "value": "2025-01-01"},
        {"field": "date", "operator": "lteq", "value": "2025-12-31"},
        {"field": "bank_account_id", "operator": "eq", "value": 1001},
    ]
    # Jamais de paramètre en clair (l'API l'ignorerait en silence).
    assert set(params.keys()) == {"filter"}


@respx.mock
async def test_paginate_sans_filtre_requete_inchangee(settings: Settings) -> None:
    route = respx.get(f"{_BASE}/transactions").respond(200, json={"items": []})
    async with PennylaneClient(settings=settings) as client:
        await TransactionsResource(client).paginate()
    assert "filter" not in route.calls[0].request.url.params


@respx.mock
async def test_paginate_filtre_et_curseur_sont_conserves_de_page_en_page(
    settings: Settings,
) -> None:
    route = respx.get(f"{_BASE}/transactions").mock(
        side_effect=[
            httpx.Response(200, json={"items": [_transaction(1)], "next_cursor": "c2"}),
            httpx.Response(200, json={"items": [_transaction(2)], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        page, _, _ = await TransactionsResource(client).paginate(
            date_from="2025-01-01", cursor="c1"
        )
    assert [t.id for t in page] == [1, 2]
    premier, second = (c.request.url.params for c in route.calls)
    assert premier["cursor"] == "c1" and second["cursor"] == "c2"
    assert json.loads(second["filter"]) == [
        {"field": "date", "operator": "gteq", "value": "2025-01-01"}
    ]


# ─── matched_invoices : factures rattachées à une transaction ────────────────


@respx.mock
async def test_matched_invoices_suit_le_curseur(settings: Settings) -> None:
    route = respx.get(f"{_BASE}/transactions/1001/matched_invoices").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [
                        {"id": 42, "type": "supplier", "url": f"{_BASE}/supplier_invoices/42"}
                    ],
                    "has_more": True,
                    "next_cursor": "m2",
                },
            ),
            httpx.Response(
                200,
                json={
                    "items": [{"id": 7, "type": "customer", "url": f"{_BASE}/customer_invoices/7"}],
                    "has_more": False,
                    "next_cursor": None,
                },
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        factures = await TransactionsResource(client).matched_invoices(1001)
    assert all(isinstance(f, MatchedInvoice) for f in factures)
    assert [(f.id, f.type) for f in factures] == [(42, "supplier"), (7, "customer")]
    assert route.calls[1].request.url.params["cursor"] == "m2"


@respx.mock
async def test_matched_invoices_vide(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions/1/matched_invoices").respond(
        200, json={"items": [], "has_more": False, "next_cursor": None}
    )
    async with PennylaneClient(settings=settings) as client:
        assert await TransactionsResource(client).matched_invoices(1) == []


@respx.mock
async def test_matched_invoices_403_propage(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions/1/matched_invoices").respond(
        403, json={"status": 403, "error": "scope"}
    )
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await TransactionsResource(client).matched_invoices(1)


# ─── get(id) ─────────────────────────────────────────────────────────────────


@respx.mock
async def test_get_by_id_returns_transaction(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions/42").respond(200, json=_transaction(42))
    async with PennylaneClient(settings=settings) as client:
        t = await TransactionsResource(client).get(42)
    assert t.id == 42
    assert t.bank_account == {"id": 2000, "label": "Compte courant"}


@respx.mock
async def test_get_id_404_propagates_client_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions/99999").respond(404, json={"error": "not found"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneClientError):
            await TransactionsResource(client).get(99999)


# ─── Résilience extra="allow" + optionnels ───────────────────────────────────


@respx.mock
async def test_extra_fields_preserved(settings: Settings) -> None:
    payload = {
        "items": [
            {
                **_transaction(1),
                "lettering_state": "lettered",  # champ inattendu
            }
        ]
    }
    respx.get(f"{_BASE}/transactions").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await TransactionsResource(client).list()
    dumped = items[0].model_dump()
    assert dumped["lettering_state"] == "lettered"


@respx.mock
async def test_minimal_payload_with_only_id(settings: Settings) -> None:
    """`id` seul suffit. Tous les autres optionnels → None."""
    payload = {"items": [{"id": 7}]}
    respx.get(f"{_BASE}/transactions").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        items = await TransactionsResource(client).list()
    assert items[0].id == 7
    assert items[0].amount is None
    assert items[0].label is None
    assert items[0].bank_account is None


# ─── Propagation 401 (token invalide, distinct du 403 scope manquant) ────────


@respx.mock
async def test_propagates_401_auth_error(settings: Settings) -> None:
    respx.get(f"{_BASE}/transactions").respond(401, json={"error": "invalid"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError) as excinfo:
            await TransactionsResource(client).list()
    assert excinfo.value.status_code == 401
