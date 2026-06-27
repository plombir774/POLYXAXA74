"""Tests for V2.3 fixes: fuzzy text-query search, retry/backoff, parallel CLOB fetch."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.polymarket.client import (
    MarketNotFoundError,
    PolymarketAPIError,
    PolymarketClient,
    is_active_open_market,
    market_matches_text_query,
    rank_markets_by_text_query,
)
from app.polymarket.parser import parse_market_input


def _market_raw(
    *,
    slug: str,
    question: str,
    volume: float = 100_000,
    liquidity: float = 50_000,
    yes_price: float = 0.5,
    closed: bool = False,
    active: bool = True,
) -> dict:
    return {
        "id": slug,
        "slug": slug,
        "question": question,
        "volumeNum": volume,
        "liquidityNum": liquidity,
        "outcomePrices": [str(yes_price), str(1 - yes_price)],
        "outcomes": ["Yes", "No"],
        "closed": closed,
        "active": active,
        "acceptingOrders": True,
    }


# ---------- Fuzzy text-query search ----------

def test_market_matches_text_query_exact_normalized_match() -> None:
    raw = _market_raw(slug="btc-150k", question="Will Bitcoin hit $150k by June 30, 2026?")
    assert market_matches_text_query(raw, "Will Bitcoin hit $150k by June 30, 2026?")


def test_market_matches_text_query_subset_match() -> None:
    raw = _market_raw(slug="btc-150k", question="Will Bitcoin hit $150k by June 30, 2026?")
    # Subset of the candidate question
    assert market_matches_text_query(raw, "Bitcoin $150k")


def test_market_matches_text_query_no_match_when_unrelated() -> None:
    raw = _market_raw(slug="btc-150k", question="Will Bitcoin hit $150k by June 30, 2026?")
    assert not market_matches_text_query(raw, "Will Trump win the 2028 election?")


def test_market_matches_text_query_rejects_inactive_markets() -> None:
    raw = _market_raw(
        slug="btc-150k",
        question="Will Bitcoin hit $150k by June 30, 2026?",
        closed=True,
    )
    assert not market_matches_text_query(raw, "Bitcoin $150k")


def test_rank_markets_by_text_query_orders_by_score_then_volume() -> None:
    candidates = [
        _market_raw(slug="fifa", question="Will Uzbekistan win the 2026 FIFA World Cup?", volume=50_000_000),
        _market_raw(slug="btc-150k", question="Will Bitcoin hit $150k by June 30, 2026?", volume=20_000_000),
        _market_raw(slug="btc-100k", question="Will Bitcoin hit $100k by June 30, 2026?", volume=10_000_000),
    ]
    ranked = rank_markets_by_text_query(candidates, "Will Bitcoin hit $150k by June 30, 2026?")
    assert len(ranked) >= 1
    assert ranked[0][1]["slug"] == "btc-150k"
    assert ranked[0][0] == 1.0  # exact normalized match


def test_rank_markets_by_text_query_returns_empty_when_no_match() -> None:
    candidates = [
        _market_raw(slug="fifa", question="Will Uzbekistan win the 2026 FIFA World Cup?"),
        _market_raw(slug="btc-150k", question="Will Bitcoin hit $150k by June 30, 2026?"),
    ]
    assert rank_markets_by_text_query(candidates, "Will Trump win the 2028 election?") == []


# ---------- Parser routing ----------

def test_parser_returns_text_query_for_free_text_input() -> None:
    parsed = parse_market_input("Will Bitcoin hit $150k by June 30, 2026?")
    assert parsed.input_type == "text_query"
    assert "Bitcoin" in parsed.slug


def test_parser_returns_slug_for_simple_slug() -> None:
    parsed = parse_market_input("btc-150k")
    assert parsed.input_type == "slug"
    assert parsed.slug == "btc-150k"


def test_parser_returns_event_for_url() -> None:
    parsed = parse_market_input("https://polymarket.com/event/when-will-bitcoin-hit-150k")
    assert parsed.input_type == "event"
    assert parsed.slug == "when-will-bitcoin-hit-150k"


# ---------- Retry behavior ----------

@pytest.mark.asyncio
async def test_request_json_retries_on_5xx_then_succeeds() -> None:
    client = PolymarketClient(max_retries=2, retry_backoff_seconds=0.0)
    call_count = 0

    class FakeResponse:
        def __init__(self, status_code: int, payload):
            self.status_code = status_code
            self._payload = payload
            self.url = "https://gamma-api.polymarket.com/markets"

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return FakeResponse(503, None)
            return FakeResponse(200, [{"slug": "ok"}])

    with patch("app.polymarket.client.httpx.AsyncClient", FakeAsyncClient):
        status, data = await client._request_json(
            "https://gamma-api.polymarket.com", "/markets"
        )

    assert status == 200
    assert call_count == 3  # initial + 2 retries


@pytest.mark.asyncio
async def test_request_json_does_not_retry_on_4xx() -> None:
    client = PolymarketClient(max_retries=3, retry_backoff_seconds=0.0)
    call_count = 0

    class FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.url = "https://gamma-api.polymarket.com/markets/slug/foo"

        def json(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return FakeResponse(404)

    with patch("app.polymarket.client.httpx.AsyncClient", FakeAsyncClient):
        status, data = await client._request_json(
            "https://gamma-api.polymarket.com", "/markets/slug/foo"
        )

    assert status == 404
    assert call_count == 1  # no retries on 4xx


@pytest.mark.asyncio
async def test_fetch_market_by_text_query_returns_market_on_match() -> None:
    client = PolymarketClient(max_retries=0, retry_backoff_seconds=0.0)
    fake_markets = [
        _market_raw(slug="fifa-uzb", question="Will Uzbekistan win the 2026 FIFA World Cup?"),
        _market_raw(slug="btc-150k", question="Will Bitcoin hit $150k by June 30, 2026?"),
    ]

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.url = "https://gamma-api.polymarket.com/markets"

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse(fake_markets)

    with patch("app.polymarket.client.httpx.AsyncClient", FakeAsyncClient):
        market = await client.fetch_market_by_text_query(
            "Will Bitcoin hit $150k by June 30, 2026?"
        )

    assert market.slug == "btc-150k"
    assert "Bitcoin" in market.title


@pytest.mark.asyncio
async def test_fetch_market_by_text_query_raises_not_found_on_no_match() -> None:
    client = PolymarketClient(max_retries=0, retry_backoff_seconds=0.0)
    fake_markets = [
        _market_raw(slug="fifa-uzb", question="Will Uzbekistan win the 2026 FIFA World Cup?"),
    ]

    class FakeResponse:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload
            self.url = "https://gamma-api.polymarket.com/markets"

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, *args, **kwargs):
            return FakeResponse(fake_markets)

    with patch("app.polymarket.client.httpx.AsyncClient", FakeAsyncClient):
        with pytest.raises(MarketNotFoundError):
            await client.fetch_market_by_text_query("Will Trump win the 2028 election?")


# ---------- Parallel CLOB fetch ----------

@pytest.mark.asyncio
async def test_enrich_with_clob_data_fetches_midpoint_and_spread_in_parallel() -> None:
    client = PolymarketClient(max_retries=0, retry_backoff_seconds=0.0)
    # Build a market with no yes_price and no spread so both CLOB calls fire
    from app.polymarket.schemas import MarketData
    market = MarketData(
        market_id="1",
        slug="test",
        title="Test",
        url="https://polymarket.com/event/test",
        yes_price=None,
        no_price=None,
        spread=None,
        raw={"clobTokenIds": '["token-123"]'},
    )

    call_log: list[float] = []
    started_at: dict[str, float] = {}
    finished_at: dict[str, float] = {}

    async def fake_midpoint(token_id):
        import time
        started_at["mid"] = time.monotonic()
        await asyncio.sleep(0.05)
        finished_at["mid"] = time.monotonic()
        call_log.append(("mid", started_at["mid"], finished_at["mid"]))
        return 0.42

    async def fake_spread(token_id):
        import time
        started_at["spread"] = time.monotonic()
        await asyncio.sleep(0.05)
        finished_at["spread"] = time.monotonic()
        call_log.append(("spread", started_at["spread"], finished_at["spread"]))
        return 0.01

    with patch.object(client, "fetch_midpoint", side_effect=fake_midpoint), \
         patch.object(client, "fetch_spread", side_effect=fake_spread):
        enriched = await client.enrich_with_clob_data(market)

    assert enriched.yes_price == 0.42
    assert enriched.spread == 0.01
    # Both calls should overlap: spread start should be before mid finish
    assert len(call_log) == 2
    mid_call = next(c for c in call_log if c[0] == "mid")
    spread_call = next(c for c in call_log if c[0] == "spread")
    # Parallel: spread started before mid finished
    assert spread_call[1] < mid_call[2], "CLOB midpoint and spread should be fetched in parallel"
