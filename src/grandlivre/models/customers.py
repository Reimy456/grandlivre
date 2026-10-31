"""Modèle métier de l'endpoint `/customers` (v0.3 B.12).

Référentiel clients du dossier. Le schéma OpenAPI déclare un `oneOf`
à 2 variantes (cf. `docs/errata_openapi_pennylane.md` §2 pattern
`cb612a6d` `customers_response_with_or_without_categories`, 3
occurrences) :

- **Company customer** : champs `vat_number`, `reg_no` (entreprise).
- **Individual customer** : champs `first_name`, `last_name`
  (particulier).

Les autres champs (id, name, billing_iban, payment_conditions,
recipient, phone, reference, notes, ledger_account, emails,
billing_address, delivery_address, created_at, updated_at,
customer_type, external_reference, billing_language, mandates,
pro_account_mandates, contacts) sont **communs** aux deux variantes.

**Stratégie face au `oneOf` `cb612a6d`** : modèle
**unique** `Customer` exposant l'**union des champs** des deux
variantes, **tous optionnels** côté Pydantic. Le champ discriminant
`customer_type` permet au consommateur de router vers la branche
métier. `extra="allow"` préserve les champs API non listés (ex.
`categories` populées sur certains endpoints). Évite l'explosion
typage `Union[CompanyCustomer, IndividualCustomer]` et reste cohérent
avec les autres Resources de la lib (`SupplierInvoicesResource`,
`LedgerEntriesResource`).

Source schéma : `openapi_spec_raw.json` paths
`/api/external/v2/customers` GET (items paginés) + `/customers/{id}`
GET (variant unique), inspecté 2026-05-12.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Customer(BaseModel):
    """Client exposé par `/customers` (union company + individual).

    Le `customer_type` (`"company"` | `"individual"`) discrimine la
    nature du client côté consommateur. Tous les champs sont
    optionnels pour absorber les deux variantes `oneOf` sans
    surcontraindre la validation.
    """

    model_config = ConfigDict(extra="allow")

    # Identifiant + nom : seuls champs systématiquement présents (les
    # deux variantes les déclarent `required`).
    id: int
    name: str

    # Discriminant Company vs Individual
    customer_type: str | None = None

    # Champs spécifiques Company customer
    vat_number: str | None = None
    reg_no: str | None = None

    # Champs spécifiques Individual customer
    first_name: str | None = None
    last_name: str | None = None

    # Champs communs aux deux variantes
    billing_iban: str | None = None
    payment_conditions: str | None = None
    recipient: str | None = None
    phone: str | None = None
    reference: str | None = None
    notes: str | None = None
    ledger_account: dict[str, Any] | None = None
    emails: list[Any] | None = None
    billing_address: dict[str, Any] | None = None
    delivery_address: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    external_reference: str | None = None
    billing_language: str | None = None
    mandates: dict[str, Any] | None = None
    pro_account_mandates: dict[str, Any] | None = None
    contacts: dict[str, Any] | None = None
