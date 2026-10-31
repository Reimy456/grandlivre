"""Modèles métier de la balance générale.

Inspiration : mcp_servers/pennylane_read/tools.py:94-114 (origine interne).

`balance = debits - credits` est calculé côté client (le champ n'est pas exposé
par l'API Pennylane V2).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, computed_field

from ..parsing import parse_decimal


def _to_decimal(value: Any) -> Decimal:
    return parse_decimal(value)


# Type Decimal strict : refuse les floats, accepte string/int/Decimal.
PennyDecimal = Annotated[Decimal, BeforeValidator(_to_decimal)]


class TrialBalanceAccount(BaseModel):
    """Une ligne de la balance générale (un compte)."""

    model_config = ConfigDict(arbitrary_types_allowed=True, str_strip_whitespace=True)

    number: str
    label: str
    formatted_number: str | None = None
    debits: PennyDecimal
    credits: PennyDecimal

    @computed_field  # type: ignore[prop-decorator]
    @property
    def balance(self) -> Decimal:
        """Solde calculé : `debits - credits`.

        Convention de signe : `balance > 0` → solde débiteur ;
        `balance < 0` → solde créditeur.
        """
        return self.debits - self.credits


class TrialBalanceResponse(BaseModel):
    """Réponse complète de l'endpoint `/trial_balance`."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    exercice: str
    period_start: str
    period_end: str
    accounts: list[TrialBalanceAccount]
