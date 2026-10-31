"""Resource façade `/supplier_invoices`.

Le tool ne résout pas d'exercice fiscal (les factures sont datées librement) ;
il prend simplement une période optionnelle. La pagination cursor est appliquée.

Les filtres passent par le paramètre ``filter`` (JSON, cf. ``grandlivre.filtre``) :
``date`` (``gteq`` / ``lteq``), ``supplier_id`` (``eq``), ``payment_status`` (``eq``, valeurs
``SupplierInvoices__Response.payment_status`` : ``to_be_processed``, ``to_be_paid``,
``partially_paid``, ``payment_error``, ``payment_scheduled``, ``payment_in_progress``,
``payment_emitted``, ``payment_found``, ``paid_offline``, ``fully_paid``). L'API n'a pas de
filtre ``status`` ; passés en clair, ``date_from=`` / ``status=`` étaient ignorés en silence
(défaut corrigé le 18/10/2026).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Any

from ..client import PennylaneClient
from ..filtre import clause, clauses_periode, param_filter
from ..models.supplier_invoices import (
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierInvoicesPage,
)
from ..pagination import paginate, paginate_with_cap

DEFAULT_HARD_CAP = 500
DEFAULT_PAGE_LIMIT = 100


class SupplierInvoicesResource:
    """Façade métier pour `/supplier_invoices`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    def _build_params(
        self,
        *,
        date_from: date | str | None,
        date_to: date | str | None,
        supplier_id: str | None,
        payment_status: str | None,
    ) -> dict[str, Any]:
        """``limit`` + le paramètre ``filter`` JSON (seule forme que l'API applique)."""
        clauses = clauses_periode(date_from, date_to)
        if supplier_id is not None:
            clauses.append(clause("supplier_id", "eq", str(supplier_id)))
        if payment_status is not None:
            clauses.append(clause("payment_status", "eq", payment_status))
        return {"limit": DEFAULT_PAGE_LIMIT, **param_filter(clauses)}

    async def iterate(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        supplier_id: str | None = None,
        payment_status: str | None = None,
    ) -> AsyncIterator[SupplierInvoice]:
        """Itère sur toutes les factures fournisseurs correspondant aux filtres."""
        params = self._build_params(
            date_from=date_from,
            date_to=date_to,
            supplier_id=supplier_id,
            payment_status=payment_status,
        )
        async for item in paginate(
            self._client, "/supplier_invoices", params=params, data_key="items"
        ):
            yield SupplierInvoice.model_validate(item)

    async def list(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        supplier_id: str | None = None,
        payment_status: str | None = None,
    ) -> list[SupplierInvoice]:
        """Liste toutes les factures (matérialisation de iterate)."""
        return [
            inv
            async for inv in self.iterate(
                date_from=date_from,
                date_to=date_to,
                supplier_id=supplier_id,
                payment_status=payment_status,
            )
        ]

    async def invoice_lines(self, invoice_id: int | str) -> Sequence[SupplierInvoiceLine]:
        """Lignes d'une facture fournisseur (`GET /supplier_invoices/{id}/invoice_lines`,
        curseur suivi jusqu'au bout — une facture a rarement plus d'une page).

        Porte `imputation_dates` (période d'imputation, nullable) : signal de période
        SECONDAIRE pour les régularisations (lot 0 B0.2, journal EC entrée 1).
        """
        params = {"limit": DEFAULT_PAGE_LIMIT}
        return [
            SupplierInvoiceLine.model_validate(item)
            async for item in paginate(
                self._client,
                f"/supplier_invoices/{invoice_id}/invoice_lines",
                params=params,
                data_key="items",
            )
        ]

    async def paginate(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        supplier_id: str | None = None,
        payment_status: str | None = None,
        cursor: str | None = None,
        cap: int = DEFAULT_HARD_CAP,
    ) -> SupplierInvoicesPage:
        """Renvoie une page de factures avec cap dur."""
        params = self._build_params(
            date_from=date_from,
            date_to=date_to,
            supplier_id=supplier_id,
            payment_status=payment_status,
        )
        if cursor is not None:
            params["cursor"] = cursor

        raw, truncated, next_cursor = await paginate_with_cap(
            self._client,
            "/supplier_invoices",
            params=params,
            data_key="items",
            cap=cap,
        )

        invoices = [SupplierInvoice.model_validate(item) for item in raw]
        return SupplierInvoicesPage(
            invoices=invoices,
            truncated=truncated,
            next_cursor=next_cursor,
            count=len(invoices),
        )
