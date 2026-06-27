"""CoinGecko client — free crypto spot prices, no API key needed.

Provides BTC/ETH/SOL/etc. spot price + 24h change for crypto-related markets.
Useful for markets like "Will Bitcoin hit $150k?" — the model can now see
that BTC is currently at $X and moved Y% in 24h, so it can reason about
distance to target.

Docs: https://docs.coingecko.com/reference/simple-price
Free tier: 50 calls/min, no key required.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx


logger = logging.getLogger(__name__)

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# Map of common symbols → CoinGecko coin ids.
# Add more as needed.
COIN_ID_MAP: dict[str, str] = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "xrp": "ripple",
    "ripple": "ripple",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
    "ada": "cardano",
    "cardano": "cardano",
    "avax": "avalanche-2",
    "matic": "matic-network",
    "pol": "polygon-ecosystem-token",
    "dot": "polkadot",
    "link": "chainlink",
    "ltc": "litecoin",
    "bch": "bitcoin-cash",
    "uni": "uniswap",
    "atom": "cosmos",
    "near": "near",
    "arb": "arbitrum",
    "op": "optimism",
    "apt": "aptos",
    "sui": "sui",
    "pepe": "pepe",
    "shib": "shiba-inu",
    "wif": "dogwifcoin",
    "bonk": "bonk",
}


# Keywords that hint a market is crypto-related.
CRYPTO_KEYWORDS: tuple[str, ...] = (
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "ripple",
    "doge", "dogecoin", "cardano", "ada", "avalanche", "avax", "polygon",
    "matic", "polkadot", "dot", "chainlink", "link", "litecoin", "ltc",
    "uniswap", "uni", "cosmos", "atom", "near", "arbitrum", "arb",
    "optimism", "op", "aptos", "apt", "sui", "pepe", "shib", "wif", "bonk",
    "crypto", "token", "coin", "altcoin", "defi", "stablecoin",
)


def is_crypto_market(title: str) -> bool:
    """Quick check if a market title mentions a crypto asset."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in CRYPTO_KEYWORDS)


def extract_mentioned_coins(title: str) -> list[str]:
    """Return list of CoinGecko coin ids mentioned in the title."""
    title_lower = title.lower()
    seen: set[str] = set()
    coins: list[str] = []
    for kw, coin_id in COIN_ID_MAP.items():
        # Match as a word boundary to avoid false positives like "btc" in "btc-150k"
        if kw in title_lower and coin_id not in seen:
            # Avoid matching "ada" inside "canada" etc.
            # Use simple check: ensure not surrounded by other letters.
            idx = title_lower.find(kw)
            while idx != -1:
                before = title_lower[idx - 1] if idx > 0 else " "
                after = title_lower[idx + len(kw)] if idx + len(kw) < len(title_lower) else " "
                if not (before.isalpha() or after.isalpha()):
                    seen.add(coin_id)
                    coins.append(coin_id)
                    break
                idx = title_lower.find(kw, idx + 1)
    return coins


async def fetch_crypto_prices(
    coin_ids: list[str],
    *,
    timeout_seconds: float = 10.0,
) -> dict[str, dict[str, Any]]:
    """Fetch spot price + 24h change for a list of CoinGecko coin ids.

    Returns: {coin_id: {"usd": float, "usd_24h_change": float, "market_cap": float}}
    Empty dict on error.
    """
    if not coin_ids:
        return {}
    # Deduplicate
    unique_ids = list(dict.fromkeys(coin_ids))
    params = {
        "ids": ",".join(unique_ids),
        "vs_currencies": "usd",
        "include_24hr_change": "true",
        "include_market_cap": "true",
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = await client.get(
                f"{COINGECKO_BASE_URL}/simple/price",
                params=params,
                headers={"accept": "application/json"},
            )
            if response.status_code >= 400:
                logger.warning(
                    "coingecko_api_failed status=%s body=%s",
                    response.status_code,
                    response.text[:200],
                )
                return {}
            return response.json() or {}
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("coingecko_api_error error_class=%s msg=%s", exc.__class__.__name__, str(exc)[:200])
        return {}


def format_crypto_context(
    title: str,
    prices: dict[str, dict[str, Any]],
) -> str:
    """Format the crypto price data as a context string for the AI prompt."""
    if not prices:
        return ""
    lines: list[str] = ["Crypto spot prices (CoinGecko, real-time):"]
    for coin_id, data in prices.items():
        usd = data.get("usd")
        change_24h = data.get("usd_24h_change")
        mcap = data.get("usd_market_cap")
        if usd is None:
            continue
        symbol = coin_id.upper()
        line = f"- {symbol}: ${usd:,.2f}"
        if change_24h is not None:
            sign = "+" if change_24h >= 0 else ""
            line += f" ({sign}{change_24h:.2f}% 24h)"
        if mcap is not None:
            line += f" | mcap ${mcap/1e9:.2f}B"
        lines.append(line)
    # Add a hint about distance-to-target for "$X" markets
    import re
    price_match = re.search(r"\$(\d[\d,]*)\s*[kKmMbB]?", title)
    if price_match and prices:
        first_coin = next(iter(prices.values()))
        if "usd" in first_coin:
            target_str = price_match.group(0).replace(",", "").replace("$", "")
            try:
                # Parse targets like $150k, $1m, $250
                target = float(target_str.rstrip("kKmMbB"))
                suffix = target_str[-1].lower() if target_str[-1].lower() in "kmb" else ""
                if suffix == "k":
                    target *= 1_000
                elif suffix == "m":
                    target *= 1_000_000
                elif suffix == "b":
                    target *= 1_000_000_000
                current = first_coin["usd"]
                # Find which coin this target refers to
                coin_id = next(iter(prices.keys()))
                distance_pct = ((target - current) / current) * 100 if current > 0 else 0
                lines.append(
                    f"- Distance to {coin_id.upper()} target ${target:,.0f}: "
                    f"{distance_pct:+.1f}% from current ${current:,.2f}"
                )
            except (ValueError, ZeroDivisionError):
                pass
    return "\n".join(lines)
