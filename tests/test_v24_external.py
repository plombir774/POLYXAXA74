"""Tests for V2.4 external context aggregator + CoinGecko + FRED + FiveThirtyEight."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.external.coingecko import (
    COIN_ID_MAP,
    extract_mentioned_coins,
    format_crypto_context,
    is_crypto_market,
)
from app.external.fred import (
    FRED_SERIES,
    is_macro_market,
    relevant_series_for_title,
)
from app.external.fivethirtyeight import (
    FTE_FEEDS,
    is_political_market,
    relevant_feeds_for_title,
)
from app.external.aggregator import build_external_context


# ---------- CoinGecko detection ----------

def test_is_crypto_market_detects_bitcoin() -> None:
    assert is_crypto_market("Will Bitcoin hit $150k by June 30, 2026?")
    assert is_crypto_market("Will ETH hit $10k?")
    assert is_crypto_market("Will Solana flip Ethereum?")


def test_is_crypto_market_rejects_non_crypto() -> None:
    assert not is_crypto_market("Will Trump win the 2028 election?")
    assert not is_crypto_market("Will it rain in London tomorrow?")


def test_extract_mentioned_coins_finds_btc_and_eth() -> None:
    coins = extract_mentioned_coins("Will Bitcoin and ETH both hit new highs?")
    assert "bitcoin" in coins
    assert "ethereum" in coins


def test_extract_mentioned_coins_avoids_false_positive_in_canada() -> None:
    # "ada" should not match inside "canada"
    coins = extract_mentioned_coins("Will Canada win the World Cup?")
    assert "cardano" not in coins


def test_format_crypto_context_handles_empty_prices() -> None:
    assert format_crypto_context("Will Bitcoin hit $150k?", {}) == ""


def test_format_crypto_context_includes_distance_to_target() -> None:
    prices = {"bitcoin": {"usd": 100_000.0, "usd_24h_change": 2.5, "usd_market_cap": 2_000_000_000_000}}
    text = format_crypto_context("Will Bitcoin hit $150k?", prices)
    assert "bitcoin" in text.lower()  # CoinGecko coin_id is lowercase
    assert "$100,000" in text
    assert "150,000" in text
    assert "+50.0%" in text  # distance to target


# ---------- FRED detection ----------

def test_is_macro_market_detects_fed_markets() -> None:
    assert is_macro_market("Will the Fed cut rates in July?")
    assert is_macro_market("Will CPI print above 3%?")
    assert is_macro_market("Will GDP growth be positive in Q3?")


def test_is_macro_market_rejects_non_macro() -> None:
    assert not is_macro_market("Will Bitcoin hit $150k?")
    assert not is_macro_market("Will Trump win the election?")


def test_relevant_series_for_fed_markets() -> None:
    series = relevant_series_for_title("Will the Fed cut rates in July?")
    assert "FEDFUNDS" in series


def test_relevant_series_for_cpi_markets() -> None:
    series = relevant_series_for_title("Will CPI print above 3%?")
    assert "CPIAUCSL" in series


def test_fred_series_covers_main_indicators() -> None:
    # Make sure all the major indicators are mapped
    for sid in ("FEDFUNDS", "CPIAUCSL", "UNRATE", "GDP", "DGS10"):
        assert sid in FRED_SERIES


# ---------- FiveThirtyEight detection ----------

def test_is_political_market_detects_election() -> None:
    assert is_political_market("Will Trump win the 2028 election?")
    assert is_political_market("Will Democrats win the House in 2026?")
    assert is_political_market("Balance of Power: 2026 Midterms")


def test_is_political_market_rejects_non_political() -> None:
    assert not is_political_market("Will Bitcoin hit $150k?")
    assert not is_political_market("Will Fed cut rates?")


def test_relevant_feeds_for_approval_market() -> None:
    feeds = relevant_feeds_for_title("Trump approval rating above 40%?")
    assert "president_approval" in feeds


def test_relevant_feeds_for_balance_of_power() -> None:
    feeds = relevant_feeds_for_title("Balance of Power: 2026 Midterms")
    # Should pick at least one of these
    assert any(f in feeds for f in ("generic_ballot", "house_polls", "senate_polls"))


# ---------- Aggregator ----------

@pytest.mark.asyncio
async def test_build_external_context_returns_empty_for_unknown_market_type() -> None:
    # A market that's neither crypto, macro, nor political should return ""
    text = await build_external_context(
        "Will it rain in London tomorrow?",
        fred_api_key=None,
        timeout_seconds=1.0,
    )
    assert text == ""


@pytest.mark.asyncio
async def test_build_external_context_calls_coingecko_for_crypto_market() -> None:
    with patch(
        "app.external.aggregator.fetch_crypto_prices",
        new=AsyncMock(return_value={"bitcoin": {"usd": 100_000.0, "usd_24h_change": 1.5, "usd_market_cap": 2e12}}),
    ) as mock_fetch:
        text = await build_external_context(
            "Will Bitcoin hit $150k by June 30, 2026?",
            fred_api_key=None,
            timeout_seconds=1.0,
        )
    assert mock_fetch.called
    assert "bitcoin" in text.lower()
    assert "$100,000" in text


@pytest.mark.asyncio
async def test_build_external_context_calls_fred_for_macro_market() -> None:
    with patch(
        "app.external.fred.fetch_fred_series",
        new=AsyncMock(return_value={
            "series_id": "FEDFUNDS",
            "label": "Fed Funds Rate (%)",
            "observations": [
                {"date": "2024-09-01", "value": "5.50"},
                {"date": "2024-08-01", "value": "5.50"},
            ],
        }),
    ) as mock_fetch:
        text = await build_external_context(
            "Will the Fed cut rates in July?",
            fred_api_key="test-key",
            timeout_seconds=1.0,
        )
    assert mock_fetch.called
    assert "FRED" in text or "Fed Funds" in text
    assert "5.50" in text


@pytest.mark.asyncio
async def test_build_external_context_skips_fred_when_no_api_key() -> None:
    # Without a FRED API key, the macro section should be empty even for macro markets
    with patch("app.external.fred.fetch_fred_series", new=AsyncMock()) as mock_fetch:
        text = await build_external_context(
            "Will the Fed cut rates in July?",
            fred_api_key=None,
            timeout_seconds=1.0,
        )
    assert not mock_fetch.called
    # No FRED section in the result
    assert "FRED" not in text and "Fed Funds" not in text


@pytest.mark.asyncio
async def test_build_external_context_runs_sources_in_parallel() -> None:
    """All three sources should run concurrently when market is crypto+macro+politics."""
    # A market that triggers all three (unusual but possible — e.g. "Bitcoin ETF approval impact on Trump's approval")
    title = "Bitcoin ETF approval and Fed rate cut impact on 2026 election"

    call_log: list[float] = []
    import time

    async def fake_crypto(coin_ids, *, timeout_seconds=10.0):
        call_log.append(("crypto_start", time.monotonic()))
        await asyncio.sleep(0.05)
        call_log.append(("crypto_end", time.monotonic()))
        return {}

    async def fake_fred(series_id, api_key, *, timeout_seconds=10.0, limit=5):
        call_log.append(("fred_start", time.monotonic()))
        await asyncio.sleep(0.05)
        call_log.append(("fred_end", time.monotonic()))
        return None

    async def fake_fte(title, *, timeout_seconds=12.0):
        call_log.append(("fte_start", time.monotonic()))
        await asyncio.sleep(0.05)
        call_log.append(("fte_end", time.monotonic()))
        return ""

    with patch("app.external.aggregator.fetch_crypto_prices", side_effect=fake_crypto), \
         patch("app.external.fred.fetch_fred_series", side_effect=fake_fred), \
         patch("app.external.fivethirtyeight.fetch_political_context", side_effect=fake_fte):
        await build_external_context(
            title,
            fred_api_key="test-key",
            timeout_seconds=1.0,
        )

    # Verify parallelism: crypto + (FRED called multiple times for multiple series) + FTE
    # All sources should start before any of them finishes.
    starts = [t for t in call_log if "start" in t[0]]
    ends = [t for t in call_log if "end" in t[0]]
    # We expect at least crypto + FRED + FTE = 3+ starts (FRED may be called per-series)
    assert len(starts) >= 3, f"Expected >=3 source starts, got {len(starts)}: {call_log}"
    assert len(ends) >= 3, f"Expected >=3 source ends, got {len(ends)}: {call_log}"
    latest_start = max(s[1] for s in starts)
    earliest_end = min(e[1] for e in ends)
    assert latest_start < earliest_end, f"Sources did not run in parallel: {call_log}"
