"""Resource façade `/me` (v0.3 B.1).

Endpoint le plus simple de l'API Pennylane V2 — utile pour :
  - Validation token (smoke test rapide).
  - Inspection scopes runtime embarqués dans le Company Token.
  - Découverte identifiants user + company (utile en multi-tenant Phase 3).

Pas de pagination, pas de paramètres. Une seule requête `GET /me`.
"""

from __future__ import annotations

from ..client import PennylaneClient
from ..models.me import MeInfo


class MeResource:
    """Façade métier pour `/me`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def get(self) -> MeInfo:
        """Renvoie l'identité du porteur de token + dossier + scopes runtime."""
        response = await self._client.request("GET", "/me")
        return MeInfo.model_validate(response)
