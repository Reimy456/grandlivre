"""Resource façade `/trial_balance`.

Inspiration : mcp_servers/pennylane_read/tools.py:117-155 (origine interne).

Usage :
    async with PennylaneClient(settings=settings) as client:
        tb = await TrialBalanceResource(client).get(exercice="2025")
        for acc in tb.accounts:
            print(acc.number, acc.balance)
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..client import PennylaneClient
from ..fiscal_years import resolve_fiscal_year
from ..models.trial_balance import TrialBalanceAccount, TrialBalanceResponse
from ..pagination import paginate


class TrialBalanceResource:
    """Façade métier pour l'endpoint `/trial_balance`.

    Le tool résout les bornes `period_start` / `period_end` de l'exercice via
    `/fiscal_years` (utilitaire public `resolve_fiscal_year`), puis itère
    `/trial_balance` paginé.
    """

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def get(self, exercice: str, *, is_auxiliary: bool = False) -> TrialBalanceResponse:
        """Renvoie la balance générale de l'exercice.

        ``is_auxiliary=True`` demande aussi les comptes auxiliaires (comptes par tiers, un par
        fournisseur ou client) : paramètre ``is_auxiliary`` de la spécification de l'éditeur
        (« Whether to include auxiliary accounts or not »). Sans lui, la balance ne porte que les
        comptes collectifs (« 401 », « 411 ») — constat du 30/10/2026 sur le dossier test.
        """
        fy = await resolve_fiscal_year(self._client, exercice)

        accounts: list[TrialBalanceAccount] = []
        async for item in paginate(
            self._client,
            "/trial_balance",
            params=_params(fy.start, fy.finish, is_auxiliary),
            data_key="items",
        ):
            accounts.append(TrialBalanceAccount.model_validate(item))

        return TrialBalanceResponse(
            exercice=exercice,
            period_start=fy.start,
            period_end=fy.finish,
            accounts=accounts,
        )

    async def iterate(
        self, exercice: str, *, is_auxiliary: bool = False
    ) -> AsyncIterator[TrialBalanceAccount]:
        """Itère sur les comptes de la balance sans charger l'ensemble en mémoire."""
        fy = await resolve_fiscal_year(self._client, exercice)
        async for item in paginate(
            self._client,
            "/trial_balance",
            params=_params(fy.start, fy.finish, is_auxiliary),
            data_key="items",
        ):
            yield TrialBalanceAccount.model_validate(item)


#: Taille de page demandée (maximum de la spécification pour ``/trial_balance``) : sans elle,
#: l'éditeur sert 20 comptes par page (168 comptes = 9 allers-retours sur le dossier test).
DEFAULT_PAGE_LIMIT = 1000


def _params(start: str, finish: str, is_auxiliary: bool) -> dict[str, str]:
    """Paramètres de ``/trial_balance`` : la période et la taille de page, plus ``is_auxiliary``
    seulement quand il est demandé."""
    params = {"period_start": start, "period_end": finish, "limit": str(DEFAULT_PAGE_LIMIT)}
    if is_auxiliary:
        params["is_auxiliary"] = "true"
    return params
