from __future__ import annotations

import re

from app.analysis.classification import MarketClassification, MarketType
from app.polymarket.schemas import MarketData


CRYPTO_TERMS = ("bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol", "xrp")
POLITICS_TERMS = (
    "election",
    "president",
    "presidential",
    "prime minister",
    "congress",
    "senate",
    "parliament",
    "approval",
    "government",
    "minister",
)
MACRO_TERMS = ("fed", "fomc", "cpi", "rates", "inflation", "gdp", "recession", "oil", "gold")
SPORTS_TERMS = (
    "world cup",
    "fifa",
    "nba",
    "nfl",
    "mlb",
    "nhl",
    "soccer",
    "football",
    "tennis",
    "ufc",
    "boxing",
    "olympics",
    "esports",
    "masters",
)


def build_catalyst_queries(
    market: MarketData,
    classification: MarketClassification | None = None,
) -> list[str]:
    text = _market_text(market)
    base = _clean_query(market.title)
    queries: list[str] = [base]

    if _has_any(text, CRYPTO_TERMS):
        queries.extend(_crypto_queries(base, text))
    elif _has_any(text, POLITICS_TERMS):
        queries.extend(_politics_queries(base, text))
    elif _has_any(text, MACRO_TERMS):
        queries.extend(_macro_queries(base, text))
    elif _has_any(text, SPORTS_TERMS):
        queries.extend(_sports_queries(base, text))
    elif classification and (
        classification.has(MarketType.MEME) or classification.has(MarketType.AMBIGUOUS_RESOLUTION)
    ):
        queries.append(f"{_underlying_event_query(base)} latest news")
    else:
        queries.append(f"{base} latest news")

    if "binance" in text:
        queries.append(f"{base} Binance")
    if any(term in text for term in ("etf", "sec")) and _has_any(text, CRYPTO_TERMS):
        queries.append(f"{base} SEC ETF")
    if "fed" in text and _has_any(text, CRYPTO_TERMS):
        queries.append(f"{base} Fed rates crypto")

    return _dedupe([query for query in queries if query])


def _market_text(market: MarketData) -> str:
    raw_text = " ".join(str(value) for value in market.raw.values() if value is not None)
    return " ".join([market.title, market.slug, market.description or "", raw_text]).lower()


def _clean_query(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("?", "")).strip()


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _crypto_queries(base: str, text: str) -> list[str]:
    symbols = []
    if "bitcoin" in text or "btc" in text:
        symbols.append("BTC")
    if "ethereum" in text or "eth" in text:
        symbols.append("ETH")
    symbol_text = " ".join(symbols) if symbols else "crypto"
    return [
        f"{base} {symbol_text} latest news",
        f"{symbol_text} ETF SEC Fed catalyst",
    ]


def _politics_queries(base: str, text: str) -> list[str]:
    year = _first_year(text)
    suffix = f" {year}" if year else ""
    return [
        f"{base}{suffix} polling election news",
        f"{base}{suffix} nomination approval official source",
    ]


def _macro_queries(base: str, text: str) -> list[str]:
    topic = "Fed CPI rates inflation GDP oil gold"
    if "oil" in text:
        topic = "oil inventory OPEC prices"
    elif "gold" in text:
        topic = "gold Fed rates inflation"
    return [
        f"{base} {topic}",
        "Federal Reserve CPI inflation rates latest",
    ]


def _sports_queries(base: str, text: str) -> list[str]:
    terms = "injury lineup odds tournament"
    if "world cup" in text or "fifa" in text:
        terms = "FIFA World Cup qualifying roster injury odds"
    return [f"{base} {terms}", f"{base} tournament date latest"]


def _underlying_event_query(base: str) -> str:
    lowered = base.lower()
    for phrase in ("before gta vi", "before gta 6", "gta vi", "gta 6"):
        lowered = lowered.replace(phrase, "")
    return _clean_query(lowered) or base


def _first_year(text: str) -> str | None:
    match = re.search(r"\b20\d{2}\b", text)
    return match.group(0) if match else None


def _dedupe(queries: list[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for query in queries:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(query)
    return result
