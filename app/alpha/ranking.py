from __future__ import annotations

from app.news.models import NewsScanResult
from app.opportunity.history import CalibrationSummary
from app.opportunity.models import OpportunityCandidate


def calculate_alpha_score(
    candidate: OpportunityCandidate,
    *,
    catalyst: NewsScanResult | None = None,
    calibration_summary: CalibrationSummary | None = None,
) -> int:
    edge_component = min(100.0, max(0.0, edge_points(candidate) * 10))
    confidence = max(0, min(100, candidate.confidence_score))
    quality = max(0.0, min(100.0, candidate.quality_score - candidate.risk_score * 0.25))
    reliability = category_reliability(candidate.category, calibration_summary)
    catalyst_score = _catalyst_score(catalyst)
    score = (
        edge_component * 0.35
        + confidence * 0.22
        + quality * 0.18
        + reliability * 0.15
        + catalyst_score * 0.10
    )
    return max(0, min(100, round(score)))


def rank_alpha_opportunities(
    candidates: list[OpportunityCandidate],
    *,
    catalysts: dict[str, NewsScanResult] | None = None,
    calibration_summary: CalibrationSummary | None = None,
    limit: int = 10,
) -> list[OpportunityCandidate]:
    catalysts = catalysts or {}
    return sorted(
        candidates,
        key=lambda item: (
            calculate_alpha_score(
                item,
                catalyst=_candidate_catalyst(item, catalysts),
                calibration_summary=calibration_summary,
            ),
            edge_points(item),
            item.confidence_score,
            category_reliability(item.category, calibration_summary),
            _catalyst_score(_candidate_catalyst(item, catalysts)),
            item.quality_score,
            -item.risk_score,
        ),
        reverse=True,
    )[:limit]


def edge_points(candidate: OpportunityCandidate) -> float:
    if (
        candidate.yes_price is None
        or candidate.fair_probability_min is None
        or candidate.fair_probability_max is None
    ):
        return 0.0
    fair_mid = (candidate.fair_probability_min + candidate.fair_probability_max) / 2
    return max(0.0, (fair_mid - candidate.yes_price) * 100)


def category_reliability(
    category: str,
    calibration_summary: CalibrationSummary | None,
) -> int:
    if calibration_summary is None:
        return 55
    metrics = calibration_summary.by_category.get(category) or calibration_summary.by_category.get("other")
    return metrics.category_reliability if metrics else 55


def _candidate_catalyst(
    candidate: OpportunityCandidate,
    catalysts: dict[str, NewsScanResult],
) -> NewsScanResult | None:
    for key in (candidate.market_slug, candidate.market_id, candidate.question):
        if key and key in catalysts:
            return catalysts[key]
    return None


def _catalyst_score(catalyst: NewsScanResult | None) -> int:
    if catalyst is None or not catalyst.news_found:
        return 0
    return max(0, min(100, catalyst.catalyst_score))
