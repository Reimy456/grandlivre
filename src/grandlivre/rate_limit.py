"""Rate limiter in-process keyé par cabinet_id.

Inspiration : mcp_servers/pennylane_read/api_client.py:53-67 (origine interne).

Pattern à trois phases (briefing v2 §12) :
  1. v0.1 : aiolimiter in-process. Suffisant pour un seul worker.
  2. Phase ultérieure : coordination Redis par cabinet_id pour partage entre
     workers Celery.
  3. Production distribuée : circuit breaker + métriques par cabinet.

La signature reste stable au passage Redis : seul l'implémentation de
`_get_rate_limiter` changera, pas son contrat.
"""

from __future__ import annotations

import asyncio
import weakref

from aiolimiter import AsyncLimiter

# Limiteurs par cabinet_id, PAR BOUCLE D'ÉVÉNEMENTS : un `AsyncLimiter` est lié à la
# boucle qui l'a utilisé en premier (aiolimiter avertit « re-used across loops » et ses
# attentes internes appartiennent à cette boucle). Un appelant qui ouvre une boucle par
# lecture (`asyncio.run` dans un thread de fond) obtient donc son propre limiteur ; le
# débit par cabinet reste borné PAR BOUCLE (in-process, comme avant). Registre faible :
# les limiteurs disparaissent avec leur boucle. Hors boucle (construction synchrone,
# tests) : registre plat `_RATE_LIMITERS`, vidé entre tests via fixture.
_RATE_LIMITERS: dict[str, AsyncLimiter] = {}
_RATE_LIMITERS_PAR_BOUCLE: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[str, AsyncLimiter]
] = weakref.WeakKeyDictionary()


def _registre() -> dict[str, AsyncLimiter]:
    try:
        boucle = asyncio.get_running_loop()
    except RuntimeError:
        return _RATE_LIMITERS
    registre = _RATE_LIMITERS_PAR_BOUCLE.get(boucle)
    if registre is None:
        registre = {}
        _RATE_LIMITERS_PAR_BOUCLE[boucle] = registre
    return registre


def get_rate_limiter(cabinet_id: str, rate_per_sec: int) -> AsyncLimiter:
    """Renvoie le limiter associé à un cabinet POUR LA BOUCLE COURANTE, créé à la demande.

    `rate_per_sec=5` correspond au Company Token API Entreprise V2 ; passer à 4
    pour un Firm Token API Cabinet.
    """
    key = cabinet_id or "default"
    registre = _registre()
    limiter = registre.get(key)
    if limiter is None:
        limiter = AsyncLimiter(max_rate=rate_per_sec, time_period=1)
        registre[key] = limiter
    return limiter


def reset_rate_limiters() -> None:
    """Vide les registres de limiters in-process. Utilisé par les fixtures de test."""
    _RATE_LIMITERS.clear()
    _RATE_LIMITERS_PAR_BOUCLE.clear()
