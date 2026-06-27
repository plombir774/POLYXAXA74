from __future__ import annotations

from app.opportunity.models import OpportunityCandidate


def rank_opportunities(
    candidates: list[OpportunityCandidate],
    *,
    limit: int = 10,
) -> list[OpportunityCandidate]:
    return sorted(
        candidates,
        key=lambda item: (
            item.opportunity_score,
            _edge_mid(item),
            item.quality_score,
            item.confidence_score,
            -(item.risk_score),
        ),
        reverse=True,
    )[:limit]


def _edge_mid(candidate: OpportunityCandidate) -> float:
    if (
        candidate.yes_price is None
        or candidate.fair_probability_min is None
        or candidate.fair_probability_max is None
    ):
        return 0.0
    fair_mid = (candidate.fair_probability_min + candidate.fair_probability_max) / 2
    return (fair_mid - candidate.yes_price) * 100
