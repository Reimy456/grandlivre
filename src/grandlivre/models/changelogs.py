"""Modèle métier des endpoints `/changelogs/*` (v0.3 B.8).

Schéma uniforme capturé empiriquement 2026-05-12 SID `6afe82d0` sur
`/changelogs/ledger_entry_lines` et `/changelogs/supplier_invoices` :

    {"id": int, "operation": str, "created_at": str,
     "updated_at": str, "processed_at": str}

5 champs uniformes pour les 3 sous-endpoints
(`ledger_entry_lines`, `supplier_invoices`, `transactions`).

`operation` ∈ {"insert", "update", "delete"} (capture empirique).

**Note `transactions`** : le sous-endpoint `/changelogs/transactions`
retourne HTTP 403 (corollaire bloqueur B.7 — même scope manquant
`transactions:readonly`). Smoke post-renouvellement token 2026-05-29.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ChangelogEntry(BaseModel):
    """Entrée de changelog uniforme pour les 3 sous-endpoints.

    Sémantique de synchronisation incrémentale : `operation` indique
    l'opération qui a été appliquée au record cible, `processed_at`
    est l'horodatage Pennylane confirmant la persistance, `created_at`
    / `updated_at` sont les bornes temporelles de cette entrée.
    """

    model_config = ConfigDict(extra="allow")

    id: int
    operation: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    processed_at: str | None = None
