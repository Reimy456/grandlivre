"""Modèle métier de l'endpoint `/transactions` (v0.3 B.7).

**Bloqué côté sandbox actuelle** : Company Token courant n'a pas le
scope `transactions:readonly` (HTTP 403 capturé empiriquement
2026-05-12 SID `6afe82d0` : `{"status":403,"error":"Access to this
resource requires scope \\"transactions:all transactions:readonly\\""}`).

Le scope sera ajouté lors du renouvellement Company Token (échéance
ROADMAP §4.3 = 2026-05-29). Smoke sandbox à faire post-renouvellement.

Schéma capturé depuis `openapi_spec_raw.json` zone
`paths./api/external/v2/transactions` (21 champs canoniques).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Transaction(BaseModel):
    """Transaction bancaire exposée par `/transactions`.

    Schéma sourcé OpenAPI spec brute Pennylane V2 (2026-05-12). Tous
    les champs sauf `id` optionnels — résilience aux variations par
    type de transaction (rapprochée / non rapprochée / archivée).
    """

    model_config = ConfigDict(extra="allow")

    id: int
    label: str | None = None
    attachment_required: bool | None = None
    date: str | None = None
    outstanding_balance: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    archived_at: str | None = None
    currency: str | None = None
    currency_amount: str | None = None
    amount: str | None = None
    currency_fee: str | None = None
    fee: str | None = None
    journal: dict[str, Any] | None = None
    bank_account: dict[str, Any] | None = None
    pro_account_expense: dict[str, Any] | None = None
    customer: dict[str, Any] | None = None
    supplier: dict[str, Any] | None = None
    categories: list[Any] | None = None
    matched_invoices: dict[str, Any] | None = None
    interbank_code: str | None = None


class MatchedInvoice(BaseModel):
    """Facture rattachée à une transaction (`GET /transactions/{transaction_id}/matched_invoices`).

    Schéma de la spec OpenAPI Pennylane V2 : `id` (identifiant de la facture), `type`
    (`customer` ou `supplier`), `url` (la facture). Dans la liste `/transactions`, le champ
    `matched_invoices` ne porte que l'adresse de cette liste, sans les identifiants.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    type: str | None = None
    url: str | None = None
