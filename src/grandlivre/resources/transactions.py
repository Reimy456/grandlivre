"""Resource façade `/transactions` (v0.3 B.7).

**Bloqueur scope courant** (sandbox 2026-05-12 SID `6afe82d0`) :

    HTTP 403 → "Access to this resource requires scope
                \\"transactions:all transactions:readonly\\""

Le Company Token actuel a 8 scopes (cf. `/me`) mais ni
`transactions:readonly` ni `bank_accounts:readonly`. Le scope sera
ajouté lors du renouvellement Company Token 2026-05-29
(ROADMAP §4.3). Smoke sandbox post-renouvellement = critère de
validation effective B.7.

La resource est **fonctionnelle** côté wrapper : elle déclenche les
appels API standard et propage les exceptions typées. Les tests
respx mockent les réponses (schéma sourcé `openapi_spec_raw.json`)
sans dépendre du scope sandbox.

Dette inscrite cahier §10 : « B.7 wait scope transactions:readonly +
smoke post-renouvellement token ».

Filtres de `paginate` : ils partent dans le paramètre ``filter`` JSON (cf.
``grandlivre.filtre``), seuls champs utilisés ici parmi ceux de la spec :
``date`` (``gteq`` / ``lteq``, bornes incluses) et ``bank_account_id`` (``eq``).
"""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

from ..client import PennylaneClient
from ..filtre import clause, clauses_periode, param_filter
from ..models.transactions import MatchedInvoice, Transaction
from ..pagination import paginate, paginate_with_cap

DEFAULT_HARD_CAP = 500
DEFAULT_PAGE_LIMIT = 100


class TransactionsResource:
    """Façade métier pour `/transactions`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[Transaction]:
        """Itère sur toutes les transactions (cursor-paginé, sans cap)."""
        async for item in paginate(self._client, "/transactions"):
            yield Transaction.model_validate(item)

    async def list(self) -> list[Transaction]:
        """Liste complète (matérialise `iterate`)."""
        return [t async for t in self.iterate()]

    async def paginate(
        self,
        *,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        bank_account_id: int | None = None,
        cursor: str | None = None,
        cap: int = DEFAULT_HARD_CAP,
    ) -> tuple[builtins.list[Transaction], bool, str | None]:
        """Renvoie une page avec cap dur configurable.

        ``date_from`` / ``date_to`` (bornes incluses) et ``bank_account_id`` filtrent chez
        l'éditeur (paramètre ``filter``) ; sans filtre, la requête est inchangée.

        Retourne `(transactions, truncated, next_cursor)`.
        """
        clauses = clauses_periode(date_from, date_to)
        if bank_account_id is not None:
            clauses.append(clause("bank_account_id", "eq", bank_account_id))
        params: dict[str, Any] = param_filter(clauses)
        if cursor is not None:
            params["cursor"] = cursor

        raw, truncated, next_cursor = await paginate_with_cap(
            self._client,
            "/transactions",
            params=params if params else None,
            data_key="items",
            cap=cap,
        )
        transactions = [Transaction.model_validate(item) for item in raw]
        return transactions, truncated, next_cursor

    async def get(self, transaction_id: int) -> Transaction:
        """Renvoie le détail d'une transaction par ID via `/transactions/{id}`."""
        response = await self._client.request("GET", f"/transactions/{transaction_id}")
        return Transaction.model_validate(response)

    async def matched_invoices(self, transaction_id: int) -> builtins.list[MatchedInvoice]:
        """Factures rattachées à une transaction
        (`GET /transactions/{transaction_id}/matched_invoices`, curseur suivi jusqu'au bout) ;
        liste vide quand aucune facture n'est rattachée."""
        return [
            MatchedInvoice.model_validate(item)
            async for item in paginate(
                self._client,
                f"/transactions/{transaction_id}/matched_invoices",
                params={"limit": DEFAULT_PAGE_LIMIT},
                data_key="items",
            )
        ]
