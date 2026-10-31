"""Modèle métier de l'endpoint `/suppliers` (v0.3 B.11).

Schéma capturé depuis `openapi_spec_raw.json` 2026-05-12
(spec OpenAPI Pennylane V2, paths `/api/external/v2/suppliers` GET et
`/api/external/v2/suppliers/{id}` GET).

Champs canoniques :

    {"id": int, "name": str, "establishment_no": str?, "reg_no": str?,
     "vat_number": str, "ledger_account": object?, "emails": list,
     "iban": str, "postal_address": object, "supplier_payment_method": str?,
     "supplier_due_date_delay": int?, "supplier_due_date_rule": str?,
     "external_reference": str, "created_at": str, "updated_at": str}

`establishment_no` (SIRET) et `reg_no` (SIREN) sont strict-français,
nullables. `ledger_account` est un objet imbriqué (id + number + label)
nullable — modélisé en `dict | None` pour rester souple (sous-objet API
similaire à `LedgerAccount` mais non strict, on conserve `extra="allow"`).
`postal_address` est également un objet imbriqué non strict.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Supplier(BaseModel):
    """Fournisseur exposé par `/suppliers` et `/suppliers/{id}`."""

    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    establishment_no: str | None = None
    reg_no: str | None = None
    vat_number: str | None = None
    ledger_account: dict[str, Any] | None = None
    emails: list[str] | None = None
    iban: str | None = None
    postal_address: dict[str, Any] | None = None
    supplier_payment_method: str | None = None
    supplier_due_date_delay: int | None = None
    supplier_due_date_rule: str | None = None
    external_reference: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
