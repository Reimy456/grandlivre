"""Configuration de la bibliothèque grandlivre.

Inspiration : mcp_servers/pennylane_read/config.py (origine interne).

Toutes les variables sont surchargeables via l'environnement (préfixe vide,
noms case-insensitive) ou via un fichier `.env` à la racine du projet.

Discipline secrets : `pennylane_api_token` est typé `SecretStr` pour empêcher
toute fuite via repr/print. La récupération de la valeur réelle nécessite un
appel explicite à `.get_secret_value()`.
"""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings grandlivre (lecture depuis env + .env)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_prefix="",
    )

    pennylane_api_token: SecretStr = Field(
        default=SecretStr(""),
        description="Token API Pennylane (Company Token Bearer).",
    )
    pennylane_base_url: str = Field(
        default="https://app.pennylane.com/api/external/v2",
        description="URL de base de l'API Pennylane V2.",
    )
    pennylane_rate_limit: int = Field(
        default=5,
        description=(
            "Plafond requêtes/sec. 5 = Company Token API Entreprise V2 ; "
            "4 = Firm Token API Cabinet."
        ),
    )
    pennylane_cabinet_id: str = Field(
        default="default",
        description="Identifiant cabinet (clé du rate limiter multi-tenant).",
    )
    pennylane_use_2026_api_changes: bool = Field(
        default=True,
        description=(
            "Si True, envoie le header X-Use-2026-API-Changes: true. "
            "Cohérent avec la phase Cleanup à partir du 2026-07-01 "
            "(cf. pennylane.readme.io/docs/2026-api-changes-guide)."
        ),
    )
    pennylane_request_timeout_seconds: float = Field(
        default=30.0,
        description="Timeout HTTP par requête (secondes).",
    )


def get_settings() -> Settings:
    """Factory simple. Pas de cache LRU pour rester trivialement testable."""
    return Settings()
