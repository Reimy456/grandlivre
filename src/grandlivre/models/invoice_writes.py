"""Modèles du module write factures — rapprochement de paiement + statut.

Couvre deux opérations d'écriture, toutes deux exposées par `InvoicePaymentsResource` :

  - ``match_payment``           → ``POST /{supplier|customer}_invoices/{id}/matched_transactions``
  - ``update_payment_status``   → ``PUT  /supplier_invoices/{id}/payment_status``
  - ``mark_customer_invoice_paid`` → ``PUT /customer_invoices/{id}/mark_as_paid``

**Différence structurante avec `POST /ledger_entries`** : ces endpoints
renvoient ``204 No Content`` SANS corps — donc AUCUN ``pennylane_id`` à mémoriser
(spec OpenAPI v2.0, capture réelle 2026-06-19). Le modèle de réponse
n'est donc pas une entité créée mais un simple accusé (`InvoiceWriteAck`). La garantie
anti-doublon repose entièrement sur le couple ``(namespace, fingerprint)`` du WAL
(cf. `resources/invoice_payments.py`), pas sur un id serveur.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Type d'invoice ciblé (les deux familles exposent `/matched_transactions`).
InvoiceKind = Literal["supplier", "customer"]

# Statut de paiement accepté par `PUT /supplier_invoices/{id}/payment_status`
# (enum spec OpenAPI v2.0, capture 2026-06-19 : exactement ces deux valeurs).
SupplierPaymentStatus = Literal["paid", "to_be_paid"]
SUPPLIER_PAYMENT_STATUSES: tuple[str, ...] = ("paid", "to_be_paid")


class InvoiceWriteAck(BaseModel):
    """Accusé d'une écriture facture ``204 No Content``.

    Pas de ``pennylane_id`` : l'API renvoie 204 sans corps. ``status`` distingue une
    écriture fraîchement confirmée (``confirmed``) d'un court-circuit WAL idempotent
    (``already_confirmed`` — re-soumission de la MÊME opération → zéro POST).

    Les chemins non nominaux ne renvoient JAMAIS ce modèle :
      - doute technique (5xx / timeout / réponse illisible) → ``WALInDoubtError`` ;
      - doublon de la même opération encore en vol → ``WALDuplicatePayloadError`` ;
      - refus déterministe Pennylane (4xx / 422) → exception Pennylane typée.
    """

    model_config = ConfigDict(extra="forbid")

    operation: str
    status: Literal["confirmed", "already_confirmed"]
    detail: str
    # Sonde d'état (F-1) — renseignée quand l'opération relit la facture avant de répondre
    # « déjà fait » : ``observed_state`` = valeur lue chez Pennylane (``payment_status``
    # fournisseur, ``paid=…`` client), ``state_verified`` = True SEULEMENT si cette valeur
    # correspond au statut demandé. Sans sonde (rapprochement, ou accusé frais 204) :
    # ``state_verified=False`` et ``observed_state=None`` — l'accusé atteste l'ENVOI, pas
    # l'état courant.
    observed_state: str | None = None
    state_verified: bool = False
