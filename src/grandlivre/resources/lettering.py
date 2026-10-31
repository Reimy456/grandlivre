"""Resource façade `/ledger_entry_lines/{id}/lettered_ledger_entry_lines` (v0.3 B.9).

Lecture seule du lettrage comptable : retourne les lignes lettrées
ensemble avec une ligne donnée (rapprochement). Réponse paginée
(`items` + `next_cursor` — pagination cursor V2 standard).

**Périmètre P2 v0.3 = LECTURE SEULE**. Les endpoints
POST / DELETE `/ledger_entry_lines/lettering` (création /
suppression de lettrage) sont **différés P3** — actions écriture qui
nécessitent un scope token élargi (Company Token actuel scopes
`:readonly`).

Doctrine cahier des charges §4.2 B.9 : « à wrapper en lecture
seulement en P2, écriture lettrage en P3 si token le permet ».
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..models.lettering import LetteredLedgerEntryLine
from ..pagination import paginate


class LetteringResource:
    """Façade métier pour
    `/ledger_entry_lines/{ledger_entry_line_id}/lettered_ledger_entry_lines`.

    Lecture seule en P2. Méthodes d'écriture (`letter()`,
    `unletter()`) non implémentées — différées P3.
    """

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    @staticmethod
    def _path(ledger_entry_line_id: int) -> str:
        return f"/ledger_entry_lines/{ledger_entry_line_id}/lettered_ledger_entry_lines"

    async def get_lettered_lines(self, ledger_entry_line_id: int) -> list[LetteredLedgerEntryLine]:
        """Renvoie les lignes lettrées avec `ledger_entry_line_id`.

        GET `/ledger_entry_lines/{ledger_entry_line_id}/lettered_ledger_entry_lines`.

        Returns:
            Liste typée des lignes lettrées. Liste vide si aucun lettrage.

        Raises:
            PennylaneNotFoundError: `ledger_entry_line_id` inconnu (404).
            PennylaneAuthError: token absent / invalide / scope manquant
                (401 / 403).
        """
        path = self._path(ledger_entry_line_id)
        return [
            LetteredLedgerEntryLine.model_validate(item)
            async for item in paginate(self._client, path)
        ]

    async def iterate_lettered_lines(
        self, ledger_entry_line_id: int
    ) -> AsyncIterator[LetteredLedgerEntryLine]:
        """Itère sur les lignes lettrées sans matérialiser la liste."""
        path = self._path(ledger_entry_line_id)
        async for item in paginate(self._client, path):
            yield LetteredLedgerEntryLine.model_validate(item)
