"""Tests de la politique de retry tenacity."""

from __future__ import annotations

import httpx
import pytest
from tenacity import AsyncRetrying

from grandlivre.exceptions import (
    PennylaneClientError,
    PennylaneRateLimitError,
    PennylaneServerError,
)
from grandlivre.retry import (
    DEFAULT_MAX_ATTEMPTS,
    build_retryer,
)


def test_build_retryer_returns_async_retrying() -> None:
    r = build_retryer()
    assert isinstance(r, AsyncRetrying)


@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: PennylaneRateLimitError("rate", status_code=429),
        lambda: PennylaneServerError("server", status_code=503),
        lambda: httpx.TransportError("timeout"),
    ],
)
async def test_retryer_retries_retryable_exceptions(exc_factory):  # type: ignore[no-untyped-def]
    """Sur exception retryable, retente jusqu'à DEFAULT_MAX_ATTEMPTS puis relance."""
    calls = {"n": 0}

    async def operation() -> str:
        calls["n"] += 1
        raise exc_factory()

    retryer = build_retryer(max_attempts=3, initial_wait=0.0, max_wait=0.0)
    with pytest.raises(type(exc_factory())):
        async for attempt in retryer:
            with attempt:
                await operation()
    assert calls["n"] == 3


async def test_retryer_does_not_retry_client_error() -> None:
    """PennylaneClientError (4xx hors 429) doit échouer immédiatement."""
    calls = {"n": 0}

    async def operation() -> str:
        calls["n"] += 1
        raise PennylaneClientError("bad", status_code=400)

    retryer = build_retryer(max_attempts=5, initial_wait=0.0, max_wait=0.0)
    with pytest.raises(PennylaneClientError):
        async for attempt in retryer:
            with attempt:
                await operation()
    assert calls["n"] == 1  # un seul appel, pas de retry


async def test_retryer_succeeds_on_second_attempt() -> None:
    """Si l'opération réussit après une exception retryable, le retryer rend la valeur."""
    calls = {"n": 0}

    async def operation() -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PennylaneRateLimitError("rate", status_code=429)
        return "OK"

    retryer = build_retryer(max_attempts=5, initial_wait=0.0, max_wait=0.0)
    result = ""
    async for attempt in retryer:
        with attempt:
            result = await operation()
    assert result == "OK"
    assert calls["n"] == 2


def test_default_max_attempts_is_5() -> None:
    assert DEFAULT_MAX_ATTEMPTS == 5
