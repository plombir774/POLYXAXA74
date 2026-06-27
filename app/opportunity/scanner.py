from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from app.analysis.classification import MarketType, classify_market
from app.opportunity.models import OpportunityCandidate, OpportunityScanResult
from app.opportunity.ranking import rank_opportunities
from app.opportunity.history import CalibrationMetrics
from app.opportunity.scoring import OPPORTUNITY_CATEGORIES, build_opportunity_candidate
from app.polymarket.schemas import MarketData


MIN_LIQUIDITY = 5_000
MAX_SPREAD = 0.15
logger = logging.getLogger(__name__)


class OpportunityScanner:
    def scan(
        self,
        markets: list[MarketData],
        *,
        category: str = "all",
        previous_by_slug: dict[str, MarketData] | None = None,
        calibration_metrics: dict[str, CalibrationMetrics] | None = None,
        limit: int = 10,
    ) -> OpportunityScanResult:
        normalized = normalize_opportunity_category(category)
        previous_by_slug = previous_by_slug or {}
        filtered = filter_opportunity_markets(markets, normalized)
        logger.info(
            "EDGE_MARKETS_FILTERED requested_category=%s markets_before_filter=%s markets_after_filter=%s",
            normalized,
            len(markets),
            len(filtered),
        )
        candidates = [
            build_opportunity_candidate(
                market,
                previous_by_slug.get(market.slug or market.market_id),
                calibration_metrics,
            )
            for market in filtered
        ]
        candidates = [
            candidate
            for candidate in candidates
            if _positive_edge(candidate) and candidate.opportunity_score >= 35
        ]
        ranked = rank_opportunities(candidates, limit=limit)
        return OpportunityScanResult(
            candidates=ranked,
            markets_scanned=len(markets),
            filtered_count=len(filtered),
            qualified_count=len(candidates),
            category=normalized,
            generated_at=datetime.now(UTC),
        )


def normalize_opportunity_category(category: str | None) -> str:
    normalized = (category or "all").strip().lower()
    if normalized in {"default", ""}:
        return "all"
    if normalized not in OPPORTUNITY_CATEGORIES:
        raise ValueError("Unknown category. Use: /edge, /edge crypto, /edge politics, /edge sports, /edge macro")
    return normalized


def filter_opportunity_markets(
    markets: list[MarketData],
    category: str = "all",
) -> list[MarketData]:
    normalized = normalize_opportunity_category(category)
    seen: set[str] = set()
    filtered = []
    for market in markets:
        key = _dedupe_key(market)
        if not key or key in seen:
            continue
        seen.add(key)
        if _reject_market(market):
            continue
        candidate = build_opportunity_candidate(market)
        if normalized != "all" and candidate.category != normalized:
            continue
        filtered.append(market)
    return filtered


def _reject_market(market: MarketData) -> bool:
    classification = classify_market(market)
    if classification.has(MarketType.MEME):
        return True
    if classification.has(MarketType.LOTTERY):
        return True
    if classification.has(MarketType.LOW_LIQUIDITY):
        return True
    if market.liquidity is None or market.liquidity < MIN_LIQUIDITY:
        return True
    if market.spread is None or market.spread > MAX_SPREAD:
        return True
    if not market.active:
        return True
    text = f"{market.title} {market.description or ''}".lower()
    if any(term in text for term in ("joke", "meme", "alien", "zombie", "before gta")):
        return True
    return False


def _dedupe_key(market: MarketData) -> str:
    text = market.slug or market.title or market.market_id
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text.lower())).strip()


def _positive_edge(candidate: OpportunityCandidate) -> bool:
    if (
        candidate.yes_price is None
        or candidate.fair_probability_min is None
        or candidate.fair_probability_max is None
    ):
        return False
    return ((candidate.fair_probability_min + candidate.fair_probability_max) / 2) > candidate.yes_price
