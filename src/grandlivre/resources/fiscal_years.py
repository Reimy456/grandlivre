"""Resource façade `/fiscal_years` (v0.3 B.2).

Liste complète des exercices fiscaux d'un dossier Pennylane.

Distincte de l'utilitaire interne `grandlivre.fiscal_years.
resolve_fiscal_year` (v0.2 A.2) qui sert au matching
`exercice` → `FiscalYearInfo` côté `TrialBalanceResource` /
`LedgerEntriesResource` :
  - `FiscalYearsResource` (ici) : exposition publique complète des
    exercices (modèle riche `FiscalYear`).
  - `resolve_fiscal_year` (utilitaire) : résolution minimaliste pour
    bornes de période (modèle `FiscalYearInfo` frozen=True).

Les deux coexistent et ne se substituent pas : ils servent des
contrats différents.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..models.fiscal_years import FiscalYear
from ..pagination import paginate


class FiscalYearsResource:
    """Façade métier pour `/fiscal_years`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[FiscalYear]:
        """Itère sur tous les exercices fiscaux du dossier (cursor-paginé)."""
        async for item in paginate(self._client, "/fiscal_years"):
            yield FiscalYear.model_validate(item)

    async def list(self) -> list[FiscalYear]:
        """Renvoie l'ensemble des exercices (matérialise `iterate`)."""
        return [fy async for fy in self.iterate()]
