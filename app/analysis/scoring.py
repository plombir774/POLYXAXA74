from __future__ import annotations

from datetime import UTC, datetime

from app.analysis.classification import MarketType, classify_market
from app.polymarket.schemas import MarketData, ScoreBreakdown


def _now() -> datetime:
    return datetime.now(UTC)


def _days_until(value: datetime | None) -> float | None:
    if value is None:
        return None
    end = value if value.tzinfo else value.replace(tzinfo=UTC)
    return (end - _now()).total_seconds() / 86400


def _age_days(value: datetime | None) -> float | None:
    if value is None:
        return None
    start = value if value.tzinfo else value.replace(tzinfo=UTC)
    return (_now() - start).total_seconds() / 86400


def price_movement_score(current: MarketData, previous: MarketData | None) -> int:
    if current.yes_price is None:
        return 0
    if previous is None or previous.yes_price is None:
        return 8
    movement = abs(current.yes_price - previous.yes_price)
    if movement >= 0.10:
        return 25
    if movement >= 0.05:
        return 18
    if movement >= 0.02:
        return 10
    if movement >= 0.01:
        return 5
    return 0


def volume_spike_score(current: MarketData, previous: MarketData | None) -> int:
    current_volume = current.volume_24hr or current.volume
    previous_volume = previous.volume_24hr or previous.volume if previous else None
    if current_volume is None:
        return 0
    if previous_volume and previous_volume > 0:
        ratio = current_volume / previous_volume
        if ratio >= 3:
            return 20
        if ratio >= 2:
            return 16
        if ratio >= 1.5:
            return 12
        if ratio >= 1.2:
            return 8
        return 3
    if current_volume >= 250_000:
        return 15
    if current_volume >= 100_000:
        return 12
    if current_volume >= 25_000:
        return 8
    if current_volume >= 5_000:
        return 4
    return 1


def liquidity_score(current: MarketData) -> int:
    if current.liquidity is None:
        return 0
    if current.liquidity >= 100_000:
        return 15
    if current.liquidity >= 50_000:
        return 12
    if current.liquidity >= 10_000:
        return 8
    if current.liquidity >= 2_500:
        return 4
    return 1


def spread_score(current: MarketData) -> int:
    if current.spread is None:
        return 0
    if current.spread <= 0.01:
        return 15
    if current.spread <= 0.02:
        return 12
    if current.spread <= 0.05:
        return 8
    if current.spread <= 0.10:
        return 4
    return 1


def time_to_resolution_score(current: MarketData) -> int:
    days = _days_until(current.end_date)
    if days is None:
        return 2
    if days < 0:
        return 0
    if days < 0.25:
        return 1
    if days < 1:
        return 3
    if days <= 30:
        return 10
    if days <= 90:
        return 8
    if days <= 180:
        return 5
    return 2


def activity_score(current: MarketData) -> int:
    if not current.active:
        return 0
    if current.volume_24hr is not None:
        if current.volume_24hr >= 100_000:
            return 10
        if current.volume_24hr >= 25_000:
            return 8
        if current.volume_24hr >= 5_000:
            return 5
        return 2
    age = _age_days(current.start_date)
    if age is None:
        return 2
    if age <= 3:
        return 10
    if age <= 14:
        return 7
    if age <= 60:
        return 4
    return 2


def calculate_signal_score(
    current: MarketData,
    previous: MarketData | None = None,
    ai_confidence_placeholder: int = 0,
) -> ScoreBreakdown:
    ai_score = max(0, min(5, ai_confidence_placeholder))
    base_components = {
        "price_movement": price_movement_score(current, previous),
        "volume_spike": volume_spike_score(current, previous),
        "liquidity": liquidity_score(current),
        "spread": spread_score(current),
        "time_to_resolution": time_to_resolution_score(current),
        "activity": activity_score(current),
        "ai_confidence_placeholder": ai_score,
    }
    classification = classify_market(current)
    penalty_components = quality_penalties(current, classification.labels, base_components)
    components = {**base_components, **penalty_components}
    total = max(0, min(100, sum(components.values())))
    return ScoreBreakdown(
        total=total,
        components=components,
        reason=(
            "Signal combines movement, volume activity, liquidity, spread, time, "
            "freshness, market type penalties, and an AI confidence placeholder."
        ),
    )


def quality_penalties(
    market: MarketData,
    market_types: tuple[MarketType, ...],
    base_components: dict[str, int],
) -> dict[str, int]:
    penalties: dict[str, int] = {}
    strong_movement = base_components.get("price_movement", 0) >= 18
    strong_volume = base_components.get("volume_spike", 0) >= 16

    if MarketType.MEME in market_types:
        penalties["meme_penalty"] = -20
    if MarketType.LOTTERY in market_types or MarketType.EXTREME_PROBABILITY in market_types:
        penalties["extreme_probability_penalty"] = -12 if (strong_movement or strong_volume) else -25
    if MarketType.AMBIGUOUS_RESOLUTION in market_types:
        penalties["ambiguous_resolution_penalty"] = -15
    if MarketType.LOW_LIQUIDITY in market_types:
        penalties["low_liquidity_penalty"] = -20
    if MarketType.HIGH_VOLUME_NO_EDGE in market_types:
        penalties["high_volume_no_edge_penalty"] = -10
    if MarketType.SHORT_DEADLINE in market_types:
        penalties["short_deadline_penalty"] = -8
    if MarketType.NEWS_DRIVEN in market_types and market.volume and market.volume >= 50_000:
        penalties["news_driven_bonus"] = 5
    return penalties


def determine_verdict(
    signal_score: int,
    risk_score: int,
    market_types: tuple[MarketType, ...] | None = None,
) -> str:
    market_types = market_types or ()
    if MarketType.LOTTERY in market_types:
        return "LOTTERY STYLE"
    if MarketType.HIGH_VOLUME_NO_EDGE in market_types:
        return "HIGH VOLUME / NO EDGE"
    if risk_score >= 75 or (
        MarketType.LOW_LIQUIDITY in market_types and MarketType.AMBIGUOUS_RESOLUTION in market_types
    ):
        return "AVOID"
    if signal_score >= 80 and risk_score <= 50:
        return "STRONG SIGNAL"
    if signal_score >= 65 and risk_score <= 65:
        return "WATCH"
    if signal_score >= 55 or 45 <= risk_score <= 74:
        return "INTERESTING BUT RISKY"
    return "LOW PRIORITY"
