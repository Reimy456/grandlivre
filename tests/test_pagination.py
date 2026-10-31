"""Tests de la pagination par curseur."""

from __future__ import annotations

import httpx
import pytest
import respx

from grandlivre.client import PennylaneClient
from grandlivre.config import Settings
from grandlivre.pagination import (
    DEFAULT_DATA_KEY,
    paginate,
    paginate_with_cap,
)

# ─── paginate ─────────────────────────────────────────────────────────────


@respx.mock
async def test_paginate_single_page_no_cursor(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/x").respond(
        200, json={"items": [{"id": 1}, {"id": 2}]}
    )
    async with PennylaneClient(settings=settings) as client:
        items = [item async for item in paginate(client, "/x")]
    assert items == [{"id": 1}, {"id": 2}]


@respx.mock
async def test_paginate_multiple_pages(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/x").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1}], "next_cursor": "c1"}),
            httpx.Response(200, json={"items": [{"id": 2}], "next_cursor": "c2"}),
            httpx.Response(200, json={"items": [{"id": 3}], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = [item async for item in paginate(client, "/x")]
    assert items == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert route.calls[1].request.url.params["cursor"] == "c1"
    assert route.calls[2].request.url.params["cursor"] == "c2"


@respx.mock
async def test_paginate_cursor_null_vs_absent(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/x").respond(
        200, json={"items": [{"id": "j1"}], "next_cursor": None}
    )
    async with PennylaneClient(settings=settings) as client:
        items = [item async for item in paginate(client, "/x")]
    assert items == [{"id": "j1"}]


@respx.mock
async def test_paginate_empty_response(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/x").respond(200, json={"items": []})
    async with PennylaneClient(settings=settings) as client:
        items = [item async for item in paginate(client, "/x")]
    assert items == []


@respx.mock
async def test_paginate_429_in_middle_resumes_cursor(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/x").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1}], "next_cursor": "c1"}),
            httpx.Response(429, json={"error": "slow"}),
            httpx.Response(200, json={"items": [{"id": 2}], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = [item async for item in paginate(client, "/x")]
    assert items == [{"id": 1}, {"id": 2}]
    # Retry conserve le cursor c1.
    assert route.calls[1].request.url.params["cursor"] == "c1"
    assert route.calls[2].request.url.params["cursor"] == "c1"


@respx.mock
async def test_paginate_5xx_in_middle_resumes(settings: Settings) -> None:
    route = respx.get("https://app.pennylane.com/api/external/v2/x").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1}], "next_cursor": "c1"}),
            httpx.Response(502, json={"error": "bad gw"}),
            httpx.Response(200, json={"items": [{"id": 2}], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items = [item async for item in paginate(client, "/x")]
    assert items == [{"id": 1}, {"id": 2}]
    assert route.calls[2].request.url.params["cursor"] == "c1"


@respx.mock
async def test_paginate_custom_data_key(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/x").respond(
        200, json={"data": [{"id": 1}]}
    )
    async with PennylaneClient(settings=settings) as client:
        items = [item async for item in paginate(client, "/x", data_key="data")]
    assert items == [{"id": 1}]


# ─── paginate_with_cap ───────────────────────────────────────────────────


@respx.mock
async def test_paginate_with_cap_not_reached(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/x").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": 1}, {"id": 2}], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items, truncated, next_cursor = await paginate_with_cap(
            client, "/x", params=None, data_key="items", cap=10
        )
    assert items == [{"id": 1}, {"id": 2}]
    assert truncated is False
    assert next_cursor is None


@respx.mock
async def test_paginate_with_cap_truncated(settings: Settings) -> None:
    respx.get("https://app.pennylane.com/api/external/v2/x").mock(
        side_effect=[
            httpx.Response(
                200,
                json={"items": [{"id": i} for i in range(5)], "next_cursor": "c1"},
            ),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items, truncated, next_cursor = await paginate_with_cap(
            client, "/x", params=None, data_key="items", cap=3
        )
    assert len(items) == 3
    assert truncated is True
    assert next_cursor == "c1"


@respx.mock
async def test_paginate_with_cap_exact_no_more_pages(settings: Settings) -> None:
    """Cap atteint mais aucun next_cursor → truncated=False."""
    respx.get("https://app.pennylane.com/api/external/v2/x").mock(
        side_effect=[
            httpx.Response(200, json={"items": [{"id": i} for i in range(5)], "next_cursor": None}),
        ]
    )
    async with PennylaneClient(settings=settings) as client:
        items, truncated, next_cursor = await paginate_with_cap(
            client, "/x", params=None, data_key="items", cap=5
        )
    assert len(items) == 5
    assert truncated is False
    assert next_cursor is None


@pytest.mark.parametrize("invalid_cap", [-100, -1, 0])
async def test_paginate_with_cap_invalid_raises(settings: Settings, invalid_cap: int) -> None:
    """Garde `cap < _MIN_CAP=1` (`pagination.py:86-87`) : les valeurs négatives
    et zéro doivent toutes lever ValueError."""
    async with PennylaneClient(settings=settings) as client:
        with pytest.raises(ValueError, match="cap"):
            await paginate_with_cap(client, "/x", params=None, data_key="items", cap=invalid_cap)


def test_default_data_key_is_items() -> None:
    assert DEFAULT_DATA_KEY == "items"
