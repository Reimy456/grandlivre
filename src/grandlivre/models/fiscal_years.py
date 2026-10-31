"""Modèle métier de l'endpoint `/fiscal_years` (v0.3 B.2).

Schéma capturé empiriquement 2026-05-12 SID `6afe82d0` :

    {
      "items": [
        {"id": int, "start": "YYYY-MM-DD", "finish": "YYYY-MM-DD",
         "status": "open" | "closed",
         "created_at": "ISO timestamp", "updated_at": "ISO timestamp"}
      ],
      "has_more": false,
      "next_cursor": null,
      "total_pages": null, "current_page": null, "per_page": null,
      "total_items": null
    }

Les 4 champs `total_*` / `current_page` / `per_page` top-level sont
legacy nullables (rejetés par l'API 2026 — cf. errata A.4 §A.3) ; on
les ignore côté wrapper.

`FiscalYear` (exposition publique complète) est **distinct** de
`FiscalYearInfo` (utilitaire interne `grandlivre.fiscal_years`,
v0.2 A.2) qui reste minimaliste pour le résolveur `resolve_fiscal_year`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class FiscalYear(BaseModel):
    """Exercice fiscal exposé par `/fiscal_years` (schéma 2026 complet).

    Champs canoniques :
      - `id` : identifiant numérique Pennylane.
      - `start` / `finish` : bornes ISO YYYY-MM-DD.
      - `status` : "open" ou "closed" (capture empirique 2026-05-12).
      - `created_at` / `updated_at` : timestamps ISO.

    `extra="allow"` : résilience aux évolutions de spec (pattern cohérent
    avec `SupplierInvoice`, `MeUser`, `MeCompany`).
    """

    model_config = ConfigDict(extra="allow")

    id: int
    start: str
    finish: str
    status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
