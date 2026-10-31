"""Modèle métier de l'endpoint `/bank_accounts` (v0.3 B.14).

**Bloqué côté sandbox actuelle** : Company Token courant n'a pas le
scope `bank_accounts:readonly` (HTTP 403 capturé empiriquement
2026-05-12 SID `6afe82d0` : `{"status":403,"error":"Access to this
resource requires scope \\"bank_accounts:all bank_accounts:readonly\\""}`).
Corollaire bloqueur B.7 (cf. `resources/transactions.py`).

Le scope sera ajouté lors du renouvellement Company Token (échéance
ROADMAP §4.3 = 2026-05-29). Smoke sandbox à faire post-renouvellement.

Schéma capturé depuis `openapi_spec_raw.json` zone
`paths./api/external/v2/bank_accounts` (9 champs canoniques).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class BankAccount(BaseModel):
    """Compte bancaire exposé par `/bank_accounts`.

    Schéma sourcé OpenAPI spec brute Pennylane V2 (2026-05-12). Tous
    les champs sauf `id` optionnels — résilience aux variations par
    type de compte (actif / archivé / établissement partenaire).
    """

    model_config = ConfigDict(extra="allow")

    id: int
    name: str | None = None
    currency: str | None = None
    balance: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    bank_establishment: dict[str, Any] | None = None
    journal: dict[str, Any] | None = None
    ledger_account: dict[str, Any] | None = None
