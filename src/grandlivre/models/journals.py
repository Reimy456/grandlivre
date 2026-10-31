"""Modèle métier de l'endpoint `/journals` (v0.3 B.3).

Schéma capturé empiriquement 2026-05-12 SID `6afe82d0` :

    {"id": int, "code": str, "label": str, "type": str}

Quatre champs simples, sans timestamps. `type` ∈ {"finances", "sales",
"purchases", "miscellaneous", ...} (énum côté Pennylane non typée
formellement — on conserve `str` + `extra="allow"`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Journal(BaseModel):
    """Journal comptable exposé par `/journals`."""

    model_config = ConfigDict(extra="allow")

    id: int
    code: str
    label: str | None = None
    type: str | None = None
