"""Tests du rate limiter in-process."""

from __future__ import annotations

from aiolimiter import AsyncLimiter

from grandlivre.rate_limit import (
    _RATE_LIMITERS,
    get_rate_limiter,
    reset_rate_limiters,
)


def test_get_rate_limiter_creates_new() -> None:
    reset_rate_limiters()
    limiter = get_rate_limiter("cab-A", 5)
    assert isinstance(limiter, AsyncLimiter)
    assert "cab-A" in _RATE_LIMITERS


def test_get_rate_limiter_returns_same_per_cabinet() -> None:
    reset_rate_limiters()
    a1 = get_rate_limiter("cab-A", 5)
    a2 = get_rate_limiter("cab-A", 5)
    b = get_rate_limiter("cab-B", 5)
    assert a1 is a2
    assert a1 is not b


def test_get_rate_limiter_empty_cabinet_id_falls_back_to_default() -> None:
    reset_rate_limiters()
    a = get_rate_limiter("", 5)
    b = get_rate_limiter("default", 5)
    assert a is b


def test_reset_rate_limiters() -> None:
    get_rate_limiter("cab-X", 5)
    assert "cab-X" in _RATE_LIMITERS
    reset_rate_limiters()
    assert _RATE_LIMITERS == {}


def test_get_rate_limiter_un_limiteur_par_boucle() -> None:
    # Un limiteur est lié à la boucle qui l'utilise (aiolimiter : « re-used across loops ») :
    # deux boucles successives (asyncio.run par lecture de fond) → deux limiteurs ; dans une
    # même boucle, le même limiteur par cabinet ; le registre hors boucle reste distinct.
    import asyncio
    import warnings

    reset_rate_limiters()
    hors_boucle = get_rate_limiter("cab-A", 5)

    async def _dans_boucle() -> tuple[AsyncLimiter, AsyncLimiter]:
        a = get_rate_limiter("cab-A", 5)
        async with a:
            pass
        return a, get_rate_limiter("cab-A", 5)

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # tout avertissement aiolimiter = échec
        a1, a1bis = asyncio.run(_dans_boucle())
        a2, _ = asyncio.run(_dans_boucle())
    assert a1 is a1bis
    assert a1 is not a2
    assert hors_boucle is not a1 and hors_boucle is get_rate_limiter("cab-A", 5)
