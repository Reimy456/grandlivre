"""Tests de MeResource (v0.3 B.1).

Schéma `/me` capturé empiriquement 2026-05-12 :
    {user: {id, first_name, last_name, email, locale},
     company: {id, name, reg_no},
     scopes: [str]}

Tous les sous-modèles ont `extra="allow"` (résilience évolution spec).
Fixture synthétique — données non-identifiantes (pas le vrai payload sandbox).
"""

from __future__ import annotations

import pytest
import respx
from pydantic import SecretStr

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.exceptions import PennylaneAuthError
from grandlivre.resources.me import MeResource

_BASE = "https://app.pennylane.com/api/external/v2"

_ME_PAYLOAD_FIXTURE = {
    "user": {
        "id": 1234,
        "first_name": "Test",
        "last_name": "User",
        "email": "test@example.invalid",
        "locale": "fr",
    },
    "company": {
        "id": 5678,
        "name": "Dossier Test",
        "reg_no": "training-test-0001",
    },
    "scopes": [
        "trial_balance:readonly",
        "fiscal_years:readonly",
        "ledger_accounts:readonly",
        "ledger_entries:readonly",
        "journals:readonly",
        "supplier_invoices:readonly",
        "suppliers:readonly",
        "customers:readonly",
    ],
}


# ─── Chemin nominal ──────────────────────────────────────────────────────────


@respx.mock
async def test_me_returns_full_payload(settings: Settings) -> None:
    """Payload complet → MeInfo avec user, company, scopes typés."""
    respx.get(f"{_BASE}/me").respond(200, json=_ME_PAYLOAD_FIXTURE)
    async with PennylaneClient(settings=settings) as client:
        me = await MeResource(client).get()
    assert me.user.id == 1234
    assert me.user.first_name == "Test"
    assert me.user.email == "test@example.invalid"
    assert me.user.locale == "fr"
    assert me.company.id == 5678
    assert me.company.name == "Dossier Test"
    assert me.company.reg_no == "training-test-0001"
    assert len(me.scopes) == 8
    assert "trial_balance:readonly" in me.scopes


@respx.mock
async def test_me_scopes_list_typed_strings(settings: Settings) -> None:
    """`scopes` doit être une liste de strings."""
    respx.get(f"{_BASE}/me").respond(200, json=_ME_PAYLOAD_FIXTURE)
    async with PennylaneClient(settings=settings) as client:
        me = await MeResource(client).get()
    assert isinstance(me.scopes, list)
    for scope in me.scopes:
        assert isinstance(scope, str)


# ─── Résilience extra="allow" ────────────────────────────────────────────────


@respx.mock
async def test_me_user_extra_fields_preserved(settings: Settings) -> None:
    """Nouveau champ inattendu sur `user` → conservé (extra='allow')."""
    payload = {
        **_ME_PAYLOAD_FIXTURE,
        "user": {
            **_ME_PAYLOAD_FIXTURE["user"],
            "phone": "+33-555-0100",
        },
    }
    respx.get(f"{_BASE}/me").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        me = await MeResource(client).get()
    assert me.user.model_dump()["phone"] == "+33-555-0100"


@respx.mock
async def test_me_company_extra_fields_preserved(settings: Settings) -> None:
    """Nouveau champ inattendu sur `company` → conservé (extra='allow')."""
    payload = {
        **_ME_PAYLOAD_FIXTURE,
        "company": {
            **_ME_PAYLOAD_FIXTURE["company"],
            "vat_number": "FR12345678901",
        },
    }
    respx.get(f"{_BASE}/me").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        me = await MeResource(client).get()
    assert me.company.model_dump()["vat_number"] == "FR12345678901"


@respx.mock
async def test_me_missing_optional_user_fields(settings: Settings) -> None:
    """Champs optionnels (first_name/last_name/email/locale) absents → None."""
    payload = {
        "user": {"id": 42},
        "company": {"id": 100},
        "scopes": [],
    }
    respx.get(f"{_BASE}/me").respond(200, json=payload)
    async with PennylaneClient(settings=settings) as client:
        me = await MeResource(client).get()
    assert me.user.id == 42
    assert me.user.first_name is None
    assert me.user.email is None
    assert me.company.name is None
    assert me.scopes == []


# ─── Propagation erreurs ─────────────────────────────────────────────────────


@respx.mock
async def test_me_propagates_401_auth_error(settings: Settings) -> None:
    """Token invalide → PennylaneAuthError remonte."""
    respx.get(f"{_BASE}/me").respond(401, json={"error": "invalid token"})
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(PennylaneAuthError):
            await MeResource(client).get()


# ─── Discipline secrets ──────────────────────────────────────────────────────


@respx.mock
async def test_me_does_not_leak_token_in_repr() -> None:
    """Discipline secrets : repr/str de MeInfo ne contient pas le token Bearer."""
    SECRET = "ULTRA-SECRET-ME-TOKEN-DO-NOT-LEAK"  # pragma: allowlist secret
    s = Settings(
        pennylane_api_token=SecretStr(SECRET),
        pennylane_rate_limit=1000,
        pennylane_cabinet_id="cab-test",
    )
    respx.get(f"{_BASE}/me").respond(200, json=_ME_PAYLOAD_FIXTURE)
    async with PennylaneClient(settings=s) as client:
        me = await MeResource(client).get()
    assert SECRET not in repr(me)
    assert SECRET not in str(me)
