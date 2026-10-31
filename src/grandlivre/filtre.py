"""Paramètre ``filter`` des listes Pennylane V2.

Les listes (``/supplier_invoices``, ``/customer_invoices``, ``/transactions``…) n'acceptent
que ``cursor``, ``limit``, ``filter`` et ``sort`` (spec OpenAPI ``openapi_spec_raw.json``) ;
``filter`` est une liste JSON de clauses ``{"field", "operator", "value"}`` passée en chaîne.
Un paramètre inconnu passé en clair (``date_from=``, ``status=``…) n'est pas refusé : l'API
l'ignore en silence et rend la liste entière (constaté sur le dossier test le 16/10/2026 :
« janvier 2025 » demandé en clair → 100 factures de 2023 à 2026).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

__all__ = ["clause", "clauses_periode", "param_filter"]


def clause(field: str, operator: str, value: Any) -> dict[str, Any]:
    """Une clause du filtre (``eq``, ``gteq``, ``lteq``… selon le champ, cf. spec)."""
    return {"field": field, "operator": operator, "value": value}


def clauses_periode(
    date_from: date | str | None, date_to: date | str | None, *, champ: str = "date"
) -> list[dict[str, Any]]:
    """Bornes incluses sur ``champ`` : ``gteq`` pour le début, ``lteq`` pour la fin ; une
    borne absente ne produit pas de clause."""
    clauses: list[dict[str, Any]] = []
    if date_from is not None:
        clauses.append(clause(champ, "gteq", _iso(date_from)))
    if date_to is not None:
        clauses.append(clause(champ, "lteq", _iso(date_to)))
    return clauses


def param_filter(clauses: list[dict[str, Any]]) -> dict[str, str]:
    """``{"filter": <JSON>}`` à fusionner dans les paramètres de requête ; ``{}`` sans clause
    (l'API refuse une liste vide ? non prouvé — on ne l'envoie pas)."""
    return {"filter": json.dumps(clauses, ensure_ascii=False)} if clauses else {}


def _iso(value: date | str) -> str:
    return value.isoformat() if isinstance(value, date) else value
