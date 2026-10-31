"""Resources façades `/changelogs/*` (v0.3 B.8).

Trois sous-endpoints uniformes pour la synchronisation incrémentale :
  - `/changelogs/ledger_entry_lines`
  - `/changelogs/supplier_invoices`
  - `/changelogs/transactions`

Pattern : items `ChangelogEntry` `{id, operation, created_at,
updated_at, processed_at}`. Permet aux consommateurs de ne traiter
que les deltas depuis le dernier passage (vs scan complet).

Convention : une seule `ChangelogsResource` avec 3
méthodes nommées d'après le sous-endpoint, pour réduire l'explosion
du nombre de Resources et factoriser le pattern paginé.

**`transactions()`** : hérite du bloqueur scope B.7 — HTTP 403
`requires scope transactions:readonly`. Le wrapper est fonctionnel
(tests respx mockent les réponses 200) ; smoke sandbox 200 post-
renouvellement token 2026-05-29.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..models.changelogs import ChangelogEntry
from ..pagination import paginate


class ChangelogsResource:
    """Façades métier pour `/changelogs/{sub-endpoint}`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def _iterate_path(self, path: str) -> AsyncIterator[ChangelogEntry]:
        """Helper interne : itère un sous-endpoint changelog paginé."""
        async for item in paginate(self._client, path):
            yield ChangelogEntry.model_validate(item)

    async def ledger_entry_lines(self) -> list[ChangelogEntry]:
        """Renvoie le changelog complet des lignes d'écritures."""
        return [e async for e in self._iterate_path("/changelogs/ledger_entry_lines")]

    async def iterate_ledger_entry_lines(
        self,
    ) -> AsyncIterator[ChangelogEntry]:
        """Itère sur le changelog des lignes d'écritures sans matérialiser."""
        async for e in self._iterate_path("/changelogs/ledger_entry_lines"):
            yield e

    async def supplier_invoices(self) -> list[ChangelogEntry]:
        """Renvoie le changelog complet des factures fournisseurs."""
        return [e async for e in self._iterate_path("/changelogs/supplier_invoices")]

    async def iterate_supplier_invoices(
        self,
    ) -> AsyncIterator[ChangelogEntry]:
        """Itère sur le changelog des factures fournisseurs sans matérialiser."""
        async for e in self._iterate_path("/changelogs/supplier_invoices"):
            yield e

    async def transactions(self) -> list[ChangelogEntry]:
        """Renvoie le changelog complet des transactions bancaires.

        **Wait scope** : nécessite `transactions:readonly` côté Company
        Token (corollaire B.7). HTTP 403 reproduit en sandbox actuelle
        tant que scope absent. Smoke post-renouvellement 2026-05-29.
        """
        return [e async for e in self._iterate_path("/changelogs/transactions")]

    async def iterate_transactions(
        self,
    ) -> AsyncIterator[ChangelogEntry]:
        """Itère sur le changelog des transactions sans matérialiser."""
        async for e in self._iterate_path("/changelogs/transactions"):
            yield e
