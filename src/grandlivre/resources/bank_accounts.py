"""Resource façade `/bank_accounts` (v0.3 B.14).

**Bloqueur scope courant** (sandbox 2026-05-12 SID `6afe82d0`) :

    HTTP 403 → "Access to this resource requires scope
                \\"bank_accounts:all bank_accounts:readonly\\""

Corollaire bloqueur B.7 : le Company Token actuel a 8 scopes (cf.
`/me`) mais ni `transactions:readonly` ni `bank_accounts:readonly`.
Le scope sera ajouté lors du renouvellement Company Token 2026-05-29
(ROADMAP §4.3). Smoke sandbox post-renouvellement = critère de
validation effective B.14.

La resource est **fonctionnelle** côté wrapper : elle déclenche les
appels API standard et propage les exceptions typées. Les tests
respx mockent les réponses (schéma sourcé `openapi_spec_raw.json`)
sans dépendre du scope sandbox.

Dette inscrite cahier §10 : « B.14 wait scope bank_accounts:readonly
+ smoke post-renouvellement token » (cohérent B.7 dette unique
étendue).
"""

from __future__ import annotations

import builtins
from collections.abc import AsyncIterator
from typing import Any

from ..client import PennylaneClient
from ..models.bank_accounts import BankAccount
from ..pagination import paginate, paginate_with_cap

DEFAULT_HARD_CAP = 500


class BankAccountsResource:
    """Façade métier pour `/bank_accounts`."""

    def __init__(self, client: PennylaneClient) -> None:
        self._client = client

    async def iterate(self) -> AsyncIterator[BankAccount]:
        """Itère sur tous les comptes bancaires (cursor-paginé, sans cap)."""
        async for item in paginate(self._client, "/bank_accounts"):
            yield BankAccount.model_validate(item)

    async def list(self) -> list[BankAccount]:
        """Liste complète (matérialise `iterate`)."""
        return [a async for a in self.iterate()]

    async def paginate(
        self,
        *,
        cursor: str | None = None,
        cap: int = DEFAULT_HARD_CAP,
    ) -> tuple[builtins.list[BankAccount], bool, str | None]:
        """Renvoie une page avec cap dur configurable.

        Retourne `(bank_accounts, truncated, next_cursor)`.
        """
        params: dict[str, Any] = {}
        if cursor is not None:
            params["cursor"] = cursor

        raw, truncated, next_cursor = await paginate_with_cap(
            self._client,
            "/bank_accounts",
            params=params if params else None,
            data_key="items",
            cap=cap,
        )
        accounts = [BankAccount.model_validate(item) for item in raw]
        return accounts, truncated, next_cursor

    async def get(self, bank_account_id: int) -> BankAccount:
        """Renvoie le détail d'un compte bancaire par ID via `/bank_accounts/{id}`."""
        response = await self._client.request("GET", f"/bank_accounts/{bank_account_id}")
        return BankAccount.model_validate(response)
