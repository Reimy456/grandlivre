"""Résolution publique d'exercice fiscal vers ses bornes Pennylane.

Mission v0.2 A.2 — élimination de la duplication tri-site identifiée
en Mission F2 :
  - resources/trial_balance.py : méthode privée `_resolve_fiscal_year`
  - resources/ledger_entries.py : méthode privée `_resolve_fiscal_year`
  - mcp_servers/pennylane_read/tools.py : helper `_resolve_fiscal_year_for_mcp`

Les trois implémentations étaient strictement identiques (même appel
`/fiscal_years`, même filtre `startswith(f"{exercice}-")`, même retour
`{id, start, finish}`). Cette extraction publique les remplace toutes
par un appel unique à `resolve_fiscal_year`.

Source cahier des charges `a0b9a49` §3.A.2.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .client import PennylaneClient
from .exceptions import FiscalYearNotFoundError


class FiscalYearInfo(BaseModel):
    """Bornes d'un exercice fiscal résolu via `/fiscal_years`.

    Interface stable : `id`, `start`, `finish` typés `str` (l'API peut
    retourner `id` en `int` côté payload — coercion explicite).
    """

    model_config = ConfigDict(frozen=True)

    id: str
    start: str
    finish: str


async def resolve_fiscal_year(client: PennylaneClient, exercice: str) -> FiscalYearInfo:
    """Résout `exercice` (ex. '2025') en `FiscalYearInfo` via `/fiscal_years`.

    Itère sur les `items` retournés et matche le premier dont `start`
    commence par `f"{exercice}-"`. Lève `FiscalYearNotFoundError` si
    aucun exercice correspondant n'est trouvé.

    L'endpoint `/fiscal_years` est paginé en cursor côté API ; la
    convention héritée des trois sites refondus appelle une seule page
    (le référentiel des exercices d'un dossier est typiquement court,
    ~1-10 entrées). Comportement conservé strictement à l'identique.
    """
    response = await client.request("GET", "/fiscal_years")
    for item in response.get("items", []):
        if str(item.get("start", "")).startswith(f"{exercice}-"):
            return FiscalYearInfo(
                id=str(item["id"]),
                start=str(item["start"]),
                finish=str(item["finish"]),
            )
    raise FiscalYearNotFoundError(exercice)
