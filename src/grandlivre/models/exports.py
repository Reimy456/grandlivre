"""Modèles métier des exports FEC (v0.3 B.6).

**Pattern asynchrone POST + polling GET** (obligation légale française
CGI 286 I bis — Fichier des Écritures Comptables).

Schéma capturé depuis `openapi_spec_raw.json` zones :
  - `paths./api/external/v2/exports/fecs` (POST 201, schéma request 2
    champs requis : `period_start`, `period_end`)
  - `paths./api/external/v2/exports/fecs/{id}` (GET 200, schéma response
    5 champs : `id`, `file_url`, `status`, `created_at`, `updated_at`)

L'enum `status` est strictement `pending | ready | error` (PAS
`completed/failed/running` — sourcé openapi spec brut, vérifié
2026-05-12 SID `e5fee...`).

Le POST 201 ne renvoie PAS `file_url` (uniquement à GET /{id} quand
`status='ready'`). `extra="allow"` cohérent autres modèles
(résilience évolutions spec).

**Bloqueur scope sandbox actuel** : Company Token courant n'a pas le
scope `ledger` requis (HTTP 403 sur POST `/exports/fecs`, dérivé du
cas `transactions:readonly` similairement bloqué cf. B.7
`tests/resources/test_transactions.py:57-77`). Scope ajouté lors du
renouvellement Company Token 2026-05-29 (ROADMAP §4.3). Smoke
sandbox post-renouvellement = critère de validation effective B.6.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

# Enum exact spec OpenAPI (zone POST 201 + GET 200 `status.enum`).
FECExportStatus = Literal["pending", "ready", "error"]


class FECExportRequest(BaseModel):
    """Payload POST `/exports/fecs`.

    Schéma sourcé OpenAPI : 2 champs requis. Dates au format ISO
    `YYYY-MM-DD`. `additionalProperties=false` côté API mais le modèle
    reste permissif (`extra="allow"`) côté client pour anticiper une
    évolution.
    """

    model_config = ConfigDict(extra="allow")

    period_start: str
    period_end: str


class FECExportJob(BaseModel):
    """Job d'export FEC (POST 201 ou GET /{id} 200).

    Champs canoniques sourcés OpenAPI :
      - `id` (int64) — ID du job
      - `status` — pending | ready | error
      - `created_at` / `updated_at` (date-time ISO)
      - `file_url` — URL signée 30 min, nullable, présente uniquement
        après `status='ready'` (absente du POST 201)
    """

    model_config = ConfigDict(extra="allow")

    id: int
    status: FECExportStatus
    created_at: str
    updated_at: str
    # `file_url` absent du POST 201, présent (nullable) sur GET /{id}.
    file_url: str | None = None


# ─── B.13 Grands livres standard et analytique (v0.3) ────────────────────────
#
# Pattern strictement identique B.6 FEC :
#   - POST asynchrone → job avec `status='pending'`.
#   - Polling GET /{id} → `status` final (`ready` ou `error`).
#   - Schéma response identique au FEC (5 champs canoniques).
#
# Différence request body :
#   - `/exports/general_ledgers` → 2 champs (`period_start`, `period_end`).
#   - `/exports/analytical_general_ledgers` → 3 champs (idem + `mode`).
#
# Schémas sourcés `openapi_spec_raw.json` zones
# `paths./api/external/v2/exports/general_ledgers{,/{id}}` et
# `paths./api/external/v2/exports/analytical_general_ledgers{,/{id}}`
# (vérifié 2026-05-12).


class GeneralLedgerExportRequest(BaseModel):
    """Payload POST `/exports/general_ledgers`.

    Schéma identique `FECExportRequest`. Dates ISO `YYYY-MM-DD`.
    `extra="allow"` côté client (résilience évolution spec).
    """

    model_config = ConfigDict(extra="allow")

    period_start: str
    period_end: str


class GeneralLedgerExportJob(BaseModel):
    """Job d'export grand livre standard (POST 201 / GET /{id} 200).

    Schéma response strictement identique `FECExportJob` (5 champs
    canoniques OpenAPI : `id`, `status`, `created_at`, `updated_at`,
    `file_url` nullable). Modèle séparé pour typage explicite côté
    consommateur (`isinstance(job, GeneralLedgerExportJob)`).
    """

    model_config = ConfigDict(extra="allow")

    id: int
    status: FECExportStatus
    created_at: str
    updated_at: str
    file_url: str | None = None


class AnalyticalGeneralLedgerExportRequest(BaseModel):
    """Payload POST `/exports/analytical_general_ledgers`.

    3 champs requis (`period_start`, `period_end`, `mode`). Le champ
    `mode` est le seul écart de schéma vs `GeneralLedgerExportRequest`
    — sélectionne l'axe analytique (typé `str` côté client pour
    préserver l'évolutivité de l'enum API).
    """

    model_config = ConfigDict(extra="allow")

    period_start: str
    period_end: str
    mode: str


class AnalyticalGeneralLedgerExportJob(BaseModel):
    """Job d'export grand livre analytique (POST 201 / GET /{id} 200).

    Schéma response strictement identique `FECExportJob` /
    `GeneralLedgerExportJob`. Modèle séparé pour typage explicite.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    status: FECExportStatus
    created_at: str
    updated_at: str
    file_url: str | None = None
