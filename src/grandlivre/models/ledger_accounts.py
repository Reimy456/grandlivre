"""Modèle métier de l'endpoint `/ledger_accounts` (v0.3 B.4).

Schéma capturé empiriquement 2026-05-12 SID `6afe82d0` :

    {"id": int, "number": str, "label": str, "vat_rate": str,
     "country_alpha2": str, "type": str, "enabled": bool,
     "letterable": bool, "created_at": str, "updated_at": str}

10 champs canoniques. `number` est la clé métier (code PCG, ex.
"411100", "44564"). `vat_rate` semble être un code interne Pennylane
(ex. "DE_190"). `type` ∈ {"reserved", "custom", ...} non typé fort.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LedgerAccount(BaseModel):
    """Compte du plan comptable exposé par `/ledger_accounts`."""

    model_config = ConfigDict(extra="allow")

    id: int
    number: str
    label: str | None = None
    vat_rate: str | None = None
    country_alpha2: str | None = None
    type: str | None = None
    enabled: bool | None = None
    letterable: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None
