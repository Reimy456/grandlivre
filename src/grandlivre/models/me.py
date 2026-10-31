"""Modèle métier de l'endpoint `/me` (v0.3 B.1).

Schéma capturé empiriquement 2026-05-12 :

    {
      "user": {"id": int, "first_name": str, "last_name": str,
               "email": str, "locale": str},
      "company": {"id": int, "name": str, "reg_no": str},
      "scopes": [str, ...]
    }

Tous les sous-modèles utilisent `extra="allow"` (résilience aux évolutions
de spec Pennylane — pattern cohérent avec `SupplierInvoice`).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MeUser(BaseModel):
    """Utilisateur porteur du token (capture empirique 2026-05-12)."""

    model_config = ConfigDict(extra="allow")

    id: int
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    locale: str | None = None


class MeCompany(BaseModel):
    """Dossier comptable lié au token (Company Token mono-dossier en Phase 2)."""

    model_config = ConfigDict(extra="allow")

    id: int
    name: str | None = None
    reg_no: str | None = None


class MeInfo(BaseModel):
    """Réponse complète de `/me` : user + company + scopes runtime."""

    model_config = ConfigDict(extra="allow")

    user: MeUser
    company: MeCompany
    scopes: list[str]
