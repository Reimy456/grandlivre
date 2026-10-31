"""Resource façade `/ledger_entries` regroupées (v0.3 B.5).

**Distinct** de `LedgerEntriesResource` v0.1 qui cible `/ledger_entry_lines`
plat. Cette resource expose les **écritures comptables regroupées**
(en-têtes) — utile pour reconstituer les écritures complètes, lecture
par ID, contrôles métier (équilibre débit/crédit par écriture).

Convention `Grouped` dans le nommage pour éviter la collision avec
`LedgerEntriesResource` v0.1. La distinction est documentée cahier
`a0b9a49` §4.2 ligne B.5.
"""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator
from typing import Any

from ..client import PennylaneClient
from ..models.ledger_entries_grouped import LedgerEntryGrouped
from ..pagination import paginate, paginate_with_cap

# Cap dur cohérent avec resources existantes (`LedgerEntriesResource` et
# `SupplierInvoicesResource`). Au-delà, le caller utilise `paginate` avec
# `next_cursor` retourné dans le tuple `paginate_with_cap`.
DEFAULT_HARD_CAP = 500


class LedgerEntriesGroupedResource:
    """Façade métier pour `/ledger_entries` regroupées."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[LedgerEntryGrouped]:
        """Itère sur toutes les écritures regroupées (cursor-paginé, sans cap)."""
        async for item in paginate(self._client, "/ledger_entries"):
            yield LedgerEntryGrouped.model_validate(item)

    async def list(self) -> list[LedgerEntryGrouped]:
        """Liste complète (matérialise `iterate`)."""
        return [e async for e in self.iterate()]

    async def paginate(
        self,
        *,
        cursor: str | None = None,
        cap: int = DEFAULT_HARD_CAP,
    ) -> tuple[builtins.list[LedgerEntryGrouped], bool, str | None]:
        """Renvoie une page avec cap dur configurable.

        Retourne `(entries, truncated, next_cursor)`. Si `truncated=True`,
        le caller peut ré-invoquer avec `cursor=next_cursor` pour continuer.
        """
        params: dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor

        raw, truncated, next_cursor = await paginate_with_cap(
            self._client,
            "/ledger_entries",
            params=params if params else None,
            data_key="items",
            cap=cap,
        )
        entries = [LedgerEntryGrouped.model_validate(item) for item in raw]
        return entries, truncated, next_cursor

    async def get(self, entry_id: int) -> LedgerEntryGrouped:
        """Renvoie le détail d'une écriture par ID via `/ledger_entries/{id}`."""
        response = await self._client.request("GET", f"/ledger_entries/{entry_id}")
        return LedgerEntryGrouped.model_validate(response)
