from __future__ import annotations

import re

from app.analysis.classification import MarketClassification, MarketType, classify_market
from app.analysis.risk import calculate_risk_score
from app.analysis.scoring import calculate_signal_score
from app.polymarket.schemas import MarketData
from app.polymarket.schemas import ScoreBreakdown


MarketAnalysisRow = tuple[MarketData, ScoreBreakdown, ScoreBreakdown, MarketClassification]


TOP_FILTERS = {"default", "crypto", "politics", "macro", "sports", "all"}
MEME_VOLUME_ALLOWLIST = 10_000_000
UNKNOWN_TOP_CATEGORY_MESSAGE = (
    "Unknown category. Use: /top, /top all, /top crypto, /top politics, /top macro, /top sports"
)


FILTER_TERMS = {
    "crypto": (
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "crypto",
        "solana",
        "sol",
        "xrp",
        "doge",
        "binance",
        "coinbase",
        "etf",
        "token",
        "blockchain",
        "usdt",
        "stablecoin",
        "defi",
        "sec crypto",
        "blackrock bitcoin",
    ),
    "politics": (
        "election",
        "president",
        "presidential",
        "prime minister",
        "trump",
        "biden",
        "congress",
        "senate",
        "house",
        "supreme court",
        "minister",
        "government",
        "war",
        "nato",
        "sanctions",
        "invasion",
        "peace deal",
        "ceasefire",
        "parliament",
        "mayor",
        "governor",
        "party",
        "referendum",
        "approval rating",
    ),
    "macro": (
        "fed",
        "rate",
        "rates",
        "interest",
        "interest rate",
        "inflation",
        "cpi",
        "gdp",
        "recession",
        "unemployment",
        "oil",
        "gold",
        "dollar",
        "yield",
        "treasury",
        "market crash",
        "s&p",
        "spx",
        "nasdaq",
        "economy",
        "tariff",
        "jobs report",
        "fomc",
    ),
    "sports": (
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
        "league of legends",
        "valorant",
        "cs2",
        "dota",
        "champions league",
        "world cup",
        "super bowl",
    ),
}

SPORTS_EXCLUSION_TERMS = (
    "world cup",
    "fifa",
    "win the 2026 fifa world cup",
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
)

POLITICS_OVERRIDE_TERMS = (
    "boycott",
    "ban",
    "sanctions",
    "government",
    "minister",
    "president",
    "election",
    "war",
    "nato",
    "invasion",
    "peace deal",
    "ceasefire",
    "treaty",
    "military",
    "coup",
)


def parse_top_category(args: list[str] | tuple[str, ...] | None) -> str:
    if not args:
        return "default"
    category = " ".join(args).strip().lower()
    if category in TOP_FILTERS:
        return category
    raise ValueError(UNKNOWN_TOP_CATEGORY_MESSAGE)


def build_market_analysis_row(market: MarketData) -> MarketAnalysisRow:
    signal = calculate_signal_score(market, None)
    risk = calculate_risk_score(market)
    classification = classify_market(market, signal_score=signal.total)
    return (market, signal, risk, classification)


def filter_top_markets(
    markets: list[MarketData],
    filter_name: str = "default",
    *,
    limit: int = 10,
    meme_allow_volume_threshold: int = MEME_VOLUME_ALLOWLIST,
) -> list[MarketAnalysisRow]:
    normalized = filter_name.lower().strip() or "default"
    if normalized not in TOP_FILTERS:
        raise ValueError(UNKNOWN_TOP_CATEGORY_MESSAGE)
    rows = [build_market_analysis_row(market) for market in markets]
    if normalized != "all":
        rows = [
            row
            for row in rows
            if _matches_filter(
                row[0],
                normalized,
                row[3],
                meme_allow_volume_threshold=meme_allow_volume_threshold,
            )
        ]
    rows = sorted(rows, key=lambda row: (row[0].volume or 0, row[0].liquidity or 0), reverse=True)
    return rows[:limit]


def _matches_filter(
    market: MarketData,
    filter_name: str,
    classification: MarketClassification,
    *,
    meme_allow_volume_threshold: int,
) -> bool:
    if filter_name == "default":
        if (
            classification.has(MarketType.MEME)
            and (market.volume or 0) < meme_allow_volume_threshold
        ):
            return False
        return True
    text = _market_text(market)
    if is_sports_market(market.title, market.raw, market.description) and filter_name in {
        "crypto",
        "macro",
    }:
        return False
    if is_sports_market(market.title, market.raw, market.description) and filter_name == "politics":
        return has_explicit_political_context(market.title, market.raw, market.description)
    return any(_contains_term(text, term) for term in FILTER_TERMS[filter_name])


def is_sports_market(
    title: str | None,
    tags: object = None,
    description: str | None = None,
) -> bool:
    text = " ".join([title or "", description or "", *_flatten_raw_values(tags)]).lower()
    return any(_contains_term(text, term) for term in SPORTS_EXCLUSION_TERMS)


def has_explicit_political_context(
    title: str | None,
    tags: object = None,
    description: str | None = None,
) -> bool:
    text = " ".join([title or "", description or "", *_flatten_raw_values(tags)]).lower()
    return any(_contains_term(text, term) for term in POLITICS_OVERRIDE_TERMS)


def _contains_term(text: str, term: str) -> bool:
    normalized = _normalize_text(text)
    normalized_term = _normalize_text(term)
    if not normalized_term:
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])"
    return re.search(pattern, normalized) is not None


def _normalize_text(text: str) -> str:
    lowered = text.lower().replace("&", " and ")
    lowered = lowered.replace("s&p", "s and p")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _market_text(market: MarketData) -> str:
    raw_bits = _flatten_raw_values(market.raw)
    return " ".join([market.title or "", market.slug or "", market.description or "", *raw_bits]).lower()


def _flatten_raw_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, dict):
        bits: list[str] = []
        for key, item in value.items():
            if key.lower() in {"icon", "image", "imageurl", "banner", "bannerurl"}:
                continue
            bits.extend(_flatten_raw_values(item))
        return bits
    if isinstance(value, list):
        bits = []
        for item in value:
            bits.extend(_flatten_raw_values(item))
        return bits
    return [str(value)]
