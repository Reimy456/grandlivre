"""Resource façade `/suppliers` (v0.3 B.11).

Référentiel des fournisseurs du dossier. Méthodes paginées cohérentes
avec les autres Resources (list/iterate) + `get(id)` pour détail par
identifiant.

Le champ `supplier_id` est consommé côté `SupplierInvoicesResource`
(`/supplier_invoices`) comme filter — exposer le référentiel permet
au consommateur de résoudre côté client le nom métier → id, ou
d'enrichir les factures fournisseur (jointure id → name/SIRET/SIREN)
sans déclencher un appel par facture.

Pas de `get_by_name(...)` exposé : `name` n'est pas une clé naturelle
unique côté Pennylane (collisions possibles). Le pattern symétrique
`LedgerAccountsResource.get_by_number` repose sur l'unicité du code PCG,
ce qui ne se transpose pas à `name`. Consommateur doit faire le mapping
côté client si nécessaire.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..models.suppliers import Supplier
from ..pagination import paginate


class SuppliersResource:
    """Façade métier pour `/suppliers`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[Supplier]:
        """Itère sur tous les fournisseurs du dossier (cursor-paginé)."""
        async for item in paginate(self._client, "/suppliers"):
            yield Supplier.model_validate(item)

    async def list(self) -> list[Supplier]:
        """Renvoie l'ensemble des fournisseurs (matérialise `iterate`)."""
        return [s async for s in self.iterate()]

    async def get(self, supplier_id: int) -> Supplier:
        """Renvoie le détail d'un fournisseur par ID via `/suppliers/{id}`."""
        response = await self._client.request("GET", f"/suppliers/{supplier_id}")
        return Supplier.model_validate(response)
