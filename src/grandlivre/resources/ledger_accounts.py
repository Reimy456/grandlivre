"""Resource façade `/ledger_accounts` (v0.3 B.4).

Plan comptable du dossier (référentiel structurant). Méthodes
paginées cohérentes avec les autres Resources + `get(id)` + résolveur
métier `get_by_number(number)` (filter JSON `[{field: number,
operator: eq, value: <number>}]` — pattern partagé avec
`LedgerEntriesResource._resolve_account_number`).

Note v0.3 (cahier `a0b9a49` §4.2 B.4) : le résolveur privé
`_resolve_account_number` côté `LedgerEntriesResource` reste interne
(coexistence acceptée — refactor de délégation explicite différé
post-B.4 si dette DRY justifie l'effort).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..exceptions import AccountNumberNotFoundError
from ..models.ledger_accounts import LedgerAccount
from ..pagination import paginate

#: Taille de page demandée (maximum de la spécification pour ``/ledger_accounts``).
DEFAULT_PAGE_LIMIT = 1000


class LedgerAccountsResource:
    """Façade métier pour `/ledger_accounts`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[LedgerAccount]:
        """Itère sur tous les comptes du plan comptable (cursor-paginé, pages de 1 000 — le maximum
        de la spécification ; sans ``limit`` l'éditeur sert 20 comptes par page : 95 allers-retours
        pour les 1 894 comptes du dossier test)."""
        async for item in paginate(
            self._client, "/ledger_accounts", params={"limit": DEFAULT_PAGE_LIMIT}
        ):
            yield LedgerAccount.model_validate(item)

    async def list(self) -> list[LedgerAccount]:
        """Renvoie l'ensemble du plan comptable (matérialise `iterate`)."""
        return [a async for a in self.iterate()]

    async def get(self, account_id: int) -> LedgerAccount:
        """Renvoie le détail d'un compte par ID via `/ledger_accounts/{id}`."""
        response = await self._client.request("GET", f"/ledger_accounts/{account_id}")
        return LedgerAccount.model_validate(response)

    async def get_by_number(self, number: str) -> LedgerAccount:
        """Résout un compte par son numéro PCG (clé métier).

        Lève `AccountNumberNotFoundError` si le numéro est absent du
        référentiel. Utilise un filter API pour réduire le payload
        (cohérent avec `LedgerEntriesResource._resolve_account_number`).
        """
        filter_clauses = [{"field": "number", "operator": "eq", "value": number}]
        response = await self._client.request(
            "GET",
            "/ledger_accounts",
            params={"filter": json.dumps(filter_clauses)},
        )
        items = response.get("items", [])
        if not items:
            raise AccountNumberNotFoundError(number)
        return LedgerAccount.model_validate(items[0])
