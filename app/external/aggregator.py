"""Aggregator that fetches external context from all configured sources in parallel.

Used by /forecast to inject real-world data into the AI prompt:
- CoinGecko (crypto prices, no key)
- FRED (macro indicators, free key)
- FiveThirtyEight (political polls, no key)

Dune Analytics integration is stubbed — needs market-specific SQL queries,
will be wired up in V2.5.

Each source returns a text snippet. They are concatenated into a single
"context block" that gets prepended to the OpenAI user message. Sources
that fail or are not configured simply return empty string.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.external.coingecko import (
    extract_mentioned_coins,
    fetch_crypto_prices,
    format_crypto_context,
    is_crypto_market,
)
from app.external.fred import fetch_macro_context, is_macro_market
from app.external.fivethirtyeight import fetch_political_context, is_political_market


logger = logging.getLogger(__name__)


async def build_external_context(
    title: str,
    *,
    fred_api_key: str | None = None,
    timeout_seconds: float = 10.0,
) -> str:
    """Fetch context from all relevant external sources, in parallel.

    Returns a single string with sections separated by blank lines.
    Empty sections are filtered out.
    """
    tasks: list[Any] = []

    # CoinGecko: only fire if market is crypto-related
    if is_crypto_market(title):
        coin_ids = extract_mentioned_coins(title)
        if coin_ids:
            tasks.append(("crypto", _fetch_crypto_block(coin_ids, timeout_seconds)))
        else:
            tasks.append(("crypto", _empty()))
    else:
        tasks.append(("crypto", _empty()))

    # FRED: only fire if market is macro-related AND key configured
    if is_macro_market(title) and fred_api_key:
        tasks.append(("macro", fetch_macro_context(title, fred_api_key, timeout_seconds=timeout_seconds)))
    else:
        tasks.append(("macro", _empty()))

    # FiveThirtyEight: only fire if market is political
    if is_political_market(title):
        tasks.append(("politics", fetch_political_context(title, timeout_seconds=timeout_seconds)))
    else:
        tasks.append(("politics", _empty()))

    # Run all in parallel
    results = await asyncio.gather(*[task for _, task in tasks], return_exceptions=True)

    sections: list[str] = []
    for (name, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.warning(
                "external_context_failed source=%s error_class=%s msg=%s",
                name,
                result.__class__.__name__,
                str(result)[:200],
            )
            continue
        if isinstance(result, str) and result.strip():
            sections.append(result.strip())

    if not sections:
        return ""
    return "\n\n".join(sections)


async def _fetch_crypto_block(coin_ids: list[str], timeout_seconds: float) -> str:
    """Wrapper that combines fetch + format for crypto context."""
    title = " ".join(coin_ids)  # placeholder title; format_crypto_context uses it for $X detection
    prices = await fetch_crypto_prices(coin_ids, timeout_seconds=timeout_seconds)
    return format_crypto_context(title, prices)


async def _empty() -> str:
    """Async no-op returning empty string."""
    return ""
