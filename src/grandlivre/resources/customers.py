"""Resource façade `/customers` (v0.3 B.12).

Référentiel clients du dossier. Méthodes paginées cohérentes avec les
autres Resources (`list/iterate`) + `get(id)` pour détail par
identifiant via `/customers/{id}`.

Le schéma réponse est un `oneOf` company/individual (cf. errata
pattern `cb612a6d`, 3 occurrences zone customers). La lib expose un
modèle **unique** `Customer` (union des champs, tous optionnels) :
voir docstring `models/customers.py` pour la stratégie complète.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..models.customers import Customer
from ..pagination import paginate


class CustomersResource:
    """Façade métier pour `/customers`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[Customer]:
        """Itère sur tous les clients du dossier (cursor-paginé)."""
        async for item in paginate(self._client, "/customers"):
            yield Customer.model_validate(item)

    async def list(self) -> list[Customer]:
        """Renvoie l'ensemble des clients (matérialise `iterate`)."""
        return [c async for c in self.iterate()]

    async def get(self, customer_id: int) -> Customer:
        """Renvoie le détail d'un client par ID via `/customers/{id}`."""
        response = await self._client.request("GET", f"/customers/{customer_id}")
        return Customer.model_validate(response)
