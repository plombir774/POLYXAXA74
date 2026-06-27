from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class OpportunityCandidate:
    market_id: str
    question: str
    category: str
    yes_price: float | None
    no_price: float | None
    volume: float | None
    liquidity: float | None
    spread: float | None
    end_date: datetime | None
    opportunity_score: int
    quality_score: int
    confidence_score: int
    risk_score: int
    edge_estimate: str
    fair_probability_min: float | None
    fair_probability_max: float | None
    market_slug: str = ""
    reason: str = ""


@dataclass(frozen=True)
class OpportunityScanResult:
    candidates: list[OpportunityCandidate] = field(default_factory=list)
    markets_scanned: int = 0
    filtered_count: int = 0
    qualified_count: int = 0
    category: str = "all"
    generated_at: datetime | None = None
