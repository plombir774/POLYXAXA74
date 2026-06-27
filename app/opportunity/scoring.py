from __future__ import annotations

import re
from datetime import UTC, datetime

from app.analysis.classification import MarketType, classify_market
from app.analysis.filtering import FILTER_TERMS, has_explicit_political_context, is_sports_market
from app.analysis.risk import calculate_risk_score
from app.opportunity.history import (
    CalibrationMetrics,
    apply_confidence_calibration,
    calibration_factor,
)
from app.polymarket.schemas import MarketData

from app.opportunity.models import OpportunityCandidate


OPPORTUNITY_CATEGORIES = {"all", "crypto", "politics", "sports", "macro"}


def build_opportunity_candidate(
    market: MarketData,
    previous: MarketData | None = None,
    calibration_metrics: dict[str, CalibrationMetrics] | None = None,
) -> OpportunityCandidate:
    category = infer_market_category(market)
    metrics = (calibration_metrics or {}).get(category) or (calibration_metrics or {}).get("other")
    quality = calculate_quality_score(market)
    raw_confidence = calculate_confidence_score(market)
    reliability = metrics.category_reliability if metrics else 55
    confidence = apply_confidence_calibration(
        raw_confidence,
        category_reliability=reliability,
        quality_score=quality,
    )
    risk = calculate_risk_score(market).total
    edge_min, edge_max = estimate_edge_points(market, previous, quality, confidence, risk)
    factor = calibration_factor(metrics)
    edge_min *= factor
    edge_max *= factor
    edge_mid = (edge_min + edge_max) / 2
    opportunity = calculate_opportunity_score(edge_mid, quality, confidence, risk)
    fair_min, fair_max = fair_probability_range(market.yes_price, edge_min, edge_max)
    return OpportunityCandidate(
        market_id=market.market_id,
        question=market.title,
        category=category,
        yes_price=market.yes_price,
        no_price=market.no_price,
        volume=market.volume,
        liquidity=market.liquidity,
        spread=market.spread,
        end_date=market.end_date,
        opportunity_score=opportunity,
        quality_score=quality,
        confidence_score=confidence,
        risk_score=risk,
        edge_estimate=_format_edge(edge_min, edge_max),
        fair_probability_min=fair_min,
        fair_probability_max=fair_max,
        market_slug=market.slug,
        reason=build_reason(market, edge_mid, quality, confidence, risk),
    )


def calculate_quality_score(market: MarketData) -> int:
    score = 0
    liquidity = market.liquidity or 0
    volume = market.volume or 0
    spread = market.spread
    price = market.yes_price

    if liquidity >= 250_000:
        score += 30
    elif liquidity >= 100_000:
        score += 25
    elif liquidity >= 50_000:
        score += 18
    elif liquidity >= 10_000:
        score += 10
    elif liquidity >= 5_000:
        score += 5

    if volume >= 5_000_000:
        score += 25
    elif volume >= 1_000_000:
        score += 22
    elif volume >= 250_000:
        score += 16
    elif volume >= 50_000:
        score += 10
    elif volume >= 10_000:
        score += 5

    if spread is not None:
        if spread <= 0.01:
            score += 25
        elif spread <= 0.02:
            score += 20
        elif spread <= 0.05:
            score += 12
        elif spread <= 0.10:
            score += 5

    if price is not None:
        if 0.20 <= price <= 0.80:
            score += 20
        elif 0.05 <= price <= 0.95:
            score += 8

    classification = classify_market(market)
    if classification.has(MarketType.MEME):
        score -= 30
    if classification.has(MarketType.LOTTERY) or classification.has(MarketType.EXTREME_PROBABILITY):
        score -= 25
    if classification.has(MarketType.LOW_LIQUIDITY):
        score -= 20
    return _clamp(score)


def calculate_confidence_score(market: MarketData) -> int:
    score = 0
    if market.liquidity is not None:
        score += min(30, int((market.liquidity / 100_000) * 20))
    if market.volume is not None:
        score += min(25, int((market.volume / 500_000) * 12))
    if market.spread is not None:
        if market.spread <= 0.01:
            score += 25
        elif market.spread <= 0.02:
            score += 20
        elif market.spread <= 0.05:
            score += 12
        elif market.spread <= 0.10:
            score += 5
    complete = sum(
        value is not None
        for value in (market.yes_price, market.volume, market.liquidity, market.spread, market.end_date)
    )
    score += complete * 4
    return _clamp(score)


def estimate_edge_points(
    market: MarketData,
    previous: MarketData | None,
    quality_score: int,
    confidence_score: int,
    risk_score: int,
) -> tuple[float, float]:
    price = market.yes_price
    if price is None:
        return 0.0, 0.0
    base = 0.0
    if 0.20 <= price <= 0.80:
        base += 2.5
    elif price < 0.05 or price > 0.95:
        base -= 3.0
    if quality_score >= 80:
        base += 3.0
    elif quality_score >= 65:
        base += 2.0
    if confidence_score >= 80:
        base += 2.0
    elif confidence_score >= 65:
        base += 1.0
    if risk_score >= 70:
        base -= 3.0
    elif risk_score >= 55:
        base -= 1.5

    movement = _price_movement(market, previous)
    if movement is not None:
        base += min(3.0, abs(movement) * 100 * 0.35)
    elif (market.volume_24hr or 0) >= 100_000:
        base += 1.0

    edge_mid = max(-5.0, min(12.0, base))
    if edge_mid <= 0:
        return edge_mid, edge_mid
    return max(0.0, edge_mid - 2.0), min(15.0, edge_mid + 2.0)


def fair_probability_range(
    yes_price: float | None,
    edge_min: float,
    edge_max: float,
) -> tuple[float | None, float | None]:
    if yes_price is None:
        return None, None
    return (
        max(0.0, min(1.0, yes_price + edge_min / 100)),
        max(0.0, min(1.0, yes_price + edge_max / 100)),
    )


def calculate_opportunity_score(
    edge_mid_points: float,
    quality_score: int,
    confidence_score: int,
    risk_score: int,
) -> int:
    edge_score = max(0, min(100, int(abs(edge_mid_points) * 10)))
    score = (
        edge_score * 0.40
        + quality_score * 0.25
        + confidence_score * 0.20
        + (100 - risk_score) * 0.15
    )
    return _clamp(round(score))


def infer_market_category(market: MarketData) -> str:
    text = _market_text(market)
    if is_sports_market(market.title, market.raw, market.description):
        if has_explicit_political_context(market.title, market.raw, market.description):
            return "politics"
        return "sports"
    for category in ("crypto", "politics", "macro"):
        if any(_contains_term(text, term) for term in FILTER_TERMS[category]):
            return category
    return "all"


def build_reason(
    market: MarketData,
    edge_mid: float,
    quality_score: int,
    confidence_score: int,
    risk_score: int,
) -> str:
    reasons = []
    if (market.liquidity or 0) >= 50_000:
        reasons.append("High liquidity.")
    if (market.volume or 0) >= 250_000:
        reasons.append("Strong volume.")
    if market.spread is not None and market.spread <= 0.03:
        reasons.append("Tight spread.")
    if market.yes_price is not None and 0.20 <= market.yes_price <= 0.80:
        reasons.append("Reasonable probability.")
    if risk_score < 55:
        reasons.append("No extreme pricing.")
    if edge_mid > 0:
        reasons.append("Heuristic fair range is above market price.")
    if not reasons:
        reasons.append("Qualified after basic quality checks.")
    return " ".join(reasons[:4])


def _price_movement(market: MarketData, previous: MarketData | None) -> float | None:
    if previous is None or previous.yes_price is None or market.yes_price is None:
        return None
    return market.yes_price - previous.yes_price


def _market_text(market: MarketData) -> str:
    return " ".join(
        [
            market.title or "",
            market.slug or "",
            market.description or "",
            " ".join(str(value) for value in market.raw.values() if value is not None),
        ]
    ).lower()


def _contains_term(text: str, term: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(term.lower().replace('&', ' and '))}(?![a-z0-9])"
    return re.search(pattern, text.lower().replace("&", " and ")) is not None


def _format_edge(edge_min: float, edge_max: float) -> str:
    if edge_min == edge_max:
        return f"{edge_min:+.0f} pts"
    return f"{edge_min:+.0f} to {edge_max:+.0f} pts"


def _clamp(value: int | float) -> int:
    return max(0, min(100, int(round(value))))
