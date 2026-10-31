"""Parsing strict des montants Pennylane.

Inspiration : mcp_servers/pennylane_read/api_client.py:70-89 (origine interne).

Pennylane renvoie les montants au format chaîne décimale (ex. "1200.50") pour
préserver la précision. La conversion implicite via `float` est interdite :
elle introduit des erreurs de représentation IEEE 754 inacceptables sur des
montants comptables (vérification renforcée par tests dédiés).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def parse_decimal(value: Any) -> Decimal:
    """Convertit une valeur Pennylane en Decimal, sans passage par float.

    Politique :
      - `None` → `ValueError` (pas de valeur par défaut implicite).
      - `Decimal` → renvoyé tel quel.
      - `int` ou `str` → `Decimal(str(value))`.
      - `float` → `TypeError` (refus explicite, perte de précision IEEE 754).
      - Tout autre type → `TypeError`.
    """
    if value is None:
        raise ValueError("Champ montant absent — pas de valeur par défaut implicite.")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # bool est sous-classe de int en Python : refus explicite.
        raise TypeError(
            "Type bool non supporté pour conversion Decimal (ambiguïté de signification)."
        )
    if isinstance(value, float):
        raise TypeError(
            f"Type float non supporté pour conversion Decimal (perte de précision IEEE 754) : {value!r}."
        )
    if isinstance(value, (int, str)):
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"Montant invalide : {value!r}") from exc
    raise TypeError(f"Type non supporté pour conversion Decimal : {type(value).__name__}.")
