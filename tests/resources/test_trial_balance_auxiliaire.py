"""Balance auxiliaire : paramètre ``is_auxiliary`` de ``TrialBalanceResource`` (30/10/2026)."""

from __future__ import annotations

from decimal import Decimal

import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.resources.trial_balance import TrialBalanceResource

BASE = "https://app.pennylane.com/api/external/v2"


@respx.mock
async def test_get_is_auxiliary_envoie_le_parametre_seulement_quand_demande(
    settings: Settings,
) -> None:
    """``is_auxiliary=True`` ajoute le paramètre de la spécification (comptes par tiers rendus) ;
    sans lui, la requête historique est inchangée (aucun paramètre ``is_auxiliary``)."""
    respx.get(f"{BASE}/fiscal_years").respond(
        200, json={"items": [{"id": 1, "start": "2025-01-01", "finish": "2025-12-31"}]}
    )
    route = respx.get(f"{BASE}/trial_balance").respond(
        200,
        json={
            "items": [
                {
                    "number": "401000042",
                    "label": "FOURNISSEUR A",
                    "debits": "0",
                    "credits": "1200.00",
                },
            ]
        },
    )

    async with PennylaneClient(settings=settings) as client:
        avec = await TrialBalanceResource(client).get(exercice="2025", is_auxiliary=True)
        sans = await TrialBalanceResource(client).get(exercice="2025")

    assert avec.accounts[0].number == "401000042"
    assert avec.accounts[0].balance == Decimal("-1200.00")
    assert len(sans.accounts) == 1
    params_avec = route.calls[0].request.url.params
    params_sans = route.calls[1].request.url.params
    assert params_avec["is_auxiliary"] == "true"
    assert params_avec["period_start"] == "2025-01-01"
    assert params_avec["period_end"] == "2025-12-31"
    assert "is_auxiliary" not in params_sans


@respx.mock
async def test_iterate_is_auxiliary(settings: Settings) -> None:
    respx.get(f"{BASE}/fiscal_years").respond(
        200, json={"items": [{"id": 1, "start": "2025-01-01", "finish": "2025-12-31"}]}
    )
    route = respx.get(f"{BASE}/trial_balance").respond(
        200,
        json={
            "items": [{"number": "411000001", "label": "CLIENT A", "debits": "500", "credits": "0"}]
        },
    )
    async with PennylaneClient(settings=settings) as client:
        comptes = [a async for a in TrialBalanceResource(client).iterate("2025", is_auxiliary=True)]
    assert [a.number for a in comptes] == ["411000001"]
    assert route.calls[0].request.url.params["is_auxiliary"] == "true"
