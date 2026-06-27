from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.classification import MarketType, classify_market
from app.polymarket.schemas import MarketData, ScoreBreakdown


AMBIGUOUS_TERMS = {
    "maybe",
    "probably",
    "likely",
    "rumor",
    "unconfirmed",
    "significant",
    "major",
}


def _days_until(value: datetime | None) -> float | None:
    if value is None:
        return None
    end = value if value.tzinfo else value.replace(tzinfo=UTC)
    return (end - datetime.now(UTC)).total_seconds() / 86400


def low_liquidity_risk(market: MarketData) -> int:
    if market.liquidity is None:
        return 12
    if market.liquidity < 1_000:
        return 20
    if market.liquidity < 5_000:
        return 15
    if market.liquidity < 10_000:
        return 10
    if market.liquidity < 50_000:
        return 5
    return 0


def wide_spread_risk(market: MarketData) -> int:
    if market.spread is None:
        return 10
    if market.spread > 0.15:
        return 20
    if market.spread > 0.10:
        return 16
    if market.spread > 0.05:
        return 10
    if market.spread > 0.02:
        return 5
    return 0


def close_resolution_risk(market: MarketData) -> int:
    days = _days_until(market.end_date)
    if days is None:
        return 5
    if days < 0:
        return 10
    if days < 0.25:
        return 15
    if days < 1:
        return 12
    if days < 3:
        return 8
    if days < 7:
        return 5
    return 0


def unclear_market_risk(market: MarketData) -> int:
    score = 0
    title = (market.title or "").strip()
    description = (market.description or "").strip()
    if not title:
        return 15
    if len(title) < 15:
        score += 8
    if not description:
        score += 5
    lowered = f"{title} {description}".lower()
    if any(term in lowered for term in AMBIGUOUS_TERMS):
        score += 5
    return min(15, score)


def extreme_price_risk(market: MarketData) -> int:
    if market.yes_price is None:
        return 5
    price = market.yes_price
    if price < 0.05 or price > 0.95:
        return 15
    if price < 0.10 or price > 0.90:
        return 10
    if price < 0.20 or price > 0.80:
        return 5
    return 0


def missing_data_risk(market: MarketData) -> int:
    fields = [
        market.yes_price,
        market.volume,
        market.liquidity,
        market.spread,
        market.end_date,
    ]
    missing = sum(1 for value in fields if value is None)
    return min(15, missing * 3)


def calculate_risk_score(market: MarketData) -> ScoreBreakdown:
    classification = classify_market(market)
    components = {
        "low_liquidity": low_liquidity_risk(market),
        "wide_spread": wide_spread_risk(market),
        "close_to_resolution": close_resolution_risk(market),
        "unclear_title_or_rules": unclear_market_risk(market),
        "extreme_price": extreme_price_risk(market),
        "missing_data": missing_data_risk(market),
        "meme_market": 12 if classification.has(MarketType.MEME) else 0,
        "lottery_market": 15 if classification.has(MarketType.LOTTERY) else 0,
        "ambiguous_resolution": 12 if classification.has(MarketType.AMBIGUOUS_RESOLUTION) else 0,
    }
    total = max(0, min(100, sum(components.values())))
    return ScoreBreakdown(
        total=total,
        components=components,
        reason=(
            "Risk combines liquidity, spread, timing, market clarity, extreme price, "
            "and missing data checks."
        ),
    )
