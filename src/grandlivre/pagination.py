"""Pagination par curseur Pennylane V2.

Inspiration : mcp_servers/pennylane_read/pagination.py (origine interne).

Pennylane V2 utilise la pagination par curseur : la réponse contient
`next_cursor` non null tant qu'il reste des pages. La sémantique
`next_cursor=None` et `next_cursor` absent sont identiques (fin de pagination).

Cas couverts :
  - page unique (`next_cursor` absent dès la première réponse) ;
  - pages multiples ;
  - `next_cursor=None` vs `next_cursor` absent (identique) ;
  - réponse vide (`data=[]`) ;
  - 429 / 5xx en milieu de pagination — retry tenacity reprend au dernier curseur.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any

from .client import PennylaneClient

# Cap minimal sentinel. Un cap < 1 n'a pas de sens.
_MIN_CAP = 1

# Nom du paramètre de curseur côté requête. Pennylane utilise "cursor".
CURSOR_PARAM = "cursor"

# Clé du curseur dans la réponse.
NEXT_CURSOR_KEY = "next_cursor"

# Clé contenant la page de résultats. Pennylane V2 utilise "items" sur les
# endpoints fiscal_years/ledger_accounts/journals/trial_balance, et "data" sur
# certains endpoints legacy. Configurable par caller.
DEFAULT_DATA_KEY = "items"


async def paginate(
    client: PennylaneClient,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    data_key: str = DEFAULT_DATA_KEY,
) -> AsyncIterator[dict[str, Any]]:
    """Itère sur tous les éléments d'une ressource paginée par curseur.

    Distinction entre `next_cursor` absent et `next_cursor=None` : les deux
    signifient « fin de pagination » et sortent de la boucle.
    """
    current_params: dict[str, Any] = dict(params or {})
    while True:
        response = await client.request("GET", path, params=current_params)

        for item in response.get(data_key, []):
            yield item

        next_cursor = response.get(NEXT_CURSOR_KEY)
        if not next_cursor:
            return

        current_params[CURSOR_PARAM] = next_cursor


async def paginate_with_cap(
    client: PennylaneClient,
    path: str,
    *,
    params: Mapping[str, Any] | None,
    data_key: str,
    cap: int,
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """Pagine avec cap dur. Retourne `(items, truncated, next_cursor_for_continuation)`.

    Itère page par page (la page entière est consommée avant le check de cap) :

    - si `len(accumulated) >= cap` ET `next_cursor` présent → break avec
      `truncated=True` et le curseur courant pour continuation.
    - si `len(accumulated) >= cap` mais sans `next_cursor` → fin pile au cap,
      `truncated=False`.
    - sinon, si `next_cursor` falsy → fin normale, `truncated=False`.

    Le caller doit aligner la taille de page (`limit=...`) avec `cap` (multiple
    de la taille de page) pour éviter le dépassement intra-page.
    """
    if cap < _MIN_CAP:
        raise ValueError(f"cap doit être >= {_MIN_CAP}, reçu {cap}.")

    current_params: dict[str, Any] = dict(params or {})
    accumulated: list[dict[str, Any]] = []
    while True:
        response = await client.request("GET", path, params=current_params)

        for item in response.get(data_key, []):
            accumulated.append(item)

        next_cursor = response.get(NEXT_CURSOR_KEY)
        if len(accumulated) >= cap:
            if next_cursor:
                return accumulated[:cap], True, next_cursor
            return accumulated[:cap], False, None
        if not next_cursor:
            return accumulated, False, None

        current_params[CURSOR_PARAM] = next_cursor
