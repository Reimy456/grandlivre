"""Modèles métier des factures clients.

Le schéma exact dépend de la spec OpenAPI Pennylane. On expose un modèle
permissif avec les champs canoniques connus + `extra="allow"` pour préserver
les champs non listés ici (résilience face aux évolutions de spec).

Symétrie avec `supplier_invoices.py` (cf. cahier des charges §4.2 B.10).
Errata `oneOf` `customer_invoices` (cf. `docs/errata_openapi_pennylane.md`,
patterns `daa153c7`, `c2efe0b7`, `2625c7fc`) concerne uniquement les POST
request bodies (création/finalisation) ; la GET response est un objet simple
sans `oneOf` (vérifié 2026-05-12 sur `openapi_spec_raw.json`).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict

from ..parsing import parse_decimal


def _to_decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return parse_decimal(value)


PennyDecimalOpt = Annotated[Decimal | None, BeforeValidator(_to_decimal_or_none)]


class CustomerInvoice(BaseModel):
    """Une facture client (`/customer_invoices`)."""

    # `extra="allow"` : on préserve les champs API non explicitement listés ici,
    # cohérent avec la doctrine no-debt (pas d'incomplétude silencieuse).
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow",
    )

    id: int | str
    invoice_number: str | None = None
    label: str | None = None
    date: str | None = None
    deadline: str | None = None
    currency: str | None = None
    amount: PennyDecimalOpt = None
    currency_amount: PennyDecimalOpt = None
    tax: PennyDecimalOpt = None
    status: str | None = None
    paid: bool | None = None
    draft: bool | None = None
    customer: dict[str, Any] | None = None


class CustomerInvoicesPage(BaseModel):
    """Une page de factures clients paginée."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    invoices: list[CustomerInvoice]
    truncated: bool = False
    next_cursor: str | None = None
    count: int


class CustomerInvoiceImportAck(BaseModel):
    """Accusé de ``POST /customer_invoices/import`` (201 + id) sous WAL.

    ``status`` : ``confirmed`` (import frais) ou ``already_confirmed`` (court-circuit WAL —
    même empreinte : corps d'import + SHA-256 du PDF — déjà confirmée, zéro envoi, facture
    mémorisée relue chez Pennylane). ``id`` = la facture chez Pennylane (fraîche ou
    mémorisée) ; ``file_attachment_id`` = la pièce déposée (``None`` au court-circuit).

    Les chemins non nominaux ne renvoient JAMAIS ce modèle : doute technique après l'import
    (5xx / timeout / réponse illisible) → ``WALInDoubtError`` ; même import encore en vol ou
    en doute → ``WALDuplicatePayloadError`` ; refus déterministe Pennylane (4xx / 422) sur la
    pièce ou l'import → exception Pennylane typée (entrée FAILED, nouvel essai autorisé).
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    status: Literal["confirmed", "already_confirmed"]
    detail: str
    invoice_number: str | None = None
    file_attachment_id: int | None = None


class CreditNoteLinkAck(BaseModel):
    """Accusé de ``POST /customer_invoices/{id}/link_credit_note`` (200) sous WAL (B2.6).

    ``status`` : ``confirmed`` (rattachement émis et accepté) ou ``already_confirmed``
    (court-circuit WAL : même facture, même avoir, déjà rattachés par ce poste — zéro envoi).
    ``invoice_id`` = la facture d'origine, ``credit_note_id`` = l'avoir rattaché ;
    ``invoice_status`` / ``remaining_amount_with_tax`` : la facture telle que l'éditeur la rend
    après le rattachement (``None`` au court-circuit). Doute technique → ``WALInDoubtError`` ;
    refus déterministe (4xx) → exception Pennylane typée (entrée FAILED, nouvel essai permis).
    """

    model_config = ConfigDict(extra="forbid")

    invoice_id: int
    credit_note_id: int
    status: Literal["confirmed", "already_confirmed"]
    detail: str
    invoice_status: str | None = None
    remaining_amount_with_tax: str | None = None
