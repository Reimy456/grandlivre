"""Resource façade `/journals` (v0.3 B.3).

Référentiel des journaux comptables du dossier. Méthodes paginées
cohérentes avec les autres Resources (list/iterate) + `get(id)` pour
détail par identifiant.

Le champ `journal_id` est déjà consommé côté `LedgerEntriesResource`
(`/ledger_entry_lines`) en filter — exposer le référentiel permet
au consommateur de résoudre `code` → `id` côté client si besoin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..models.journals import Journal
from ..pagination import paginate


class JournalsResource:
    """Façade métier pour `/journals`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[Journal]:
        """Itère sur tous les journaux du dossier (cursor-paginé)."""
        async for item in paginate(self._client, "/journals"):
            yield Journal.model_validate(item)

    async def list(self) -> list[Journal]:
        """Renvoie l'ensemble des journaux (matérialise `iterate`)."""
        return [j async for j in self.iterate()]

    async def get(self, journal_id: int) -> Journal:
        """Renvoie le détail d'un journal par ID via `/journals/{id}`."""
        response = await self._client.request("GET", f"/journals/{journal_id}")
        return Journal.model_validate(response)
