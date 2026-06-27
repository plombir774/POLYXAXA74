from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.analysis.classification import MarketClassification
from app.analysis.movement import SnapshotMovement
from app.news.models import NewsScanResult
from app.opportunity.history import CalibrationSummary
from app.opportunity.models import OpportunityCandidate
from app.polymarket.schemas import MarketData, ScoreBreakdown


@dataclass(frozen=True)
class AlphaCatalyst:
    market_id: str
    question: str
    catalyst_score: int
    sentiment: str
    confidence: int
    explanation: str


@dataclass(frozen=True)
class AlphaWatchlistCandidate:
    market: MarketData
    signal: ScoreBreakdown
    risk: ScoreBreakdown
    classification: MarketClassification
    catalyst: NewsScanResult | None = None


@dataclass(frozen=True)
class WatchlistAlert:
    market: MarketData
    signal_score: int
    catalyst_score: int
    risk_score: int
    verdict: str


@dataclass(frozen=True)
class AlphaMarketMovement:
    market: MarketData
    movement: SnapshotMovement


@dataclass(frozen=True)
class MarketMover:
    market: MarketData
    change_24h: float
    current_probability: float | None


@dataclass(frozen=True)
class AlphaRiskMarket:
    market_id: str
    question: str
    risk_score: int
    reason: str


@dataclass(frozen=True)
class AlphaReport:
    generated_at: datetime
    category: str
    alpha_score: int
    top_opportunity: OpportunityCandidate | None = None
    strongest_catalyst: AlphaCatalyst | None = None
    watchlist_alert: WatchlistAlert | None = None
    upward_movers: list[MarketMover] = field(default_factory=list)
    downward_movers: list[MarketMover] = field(default_factory=list)
    highest_risk_market: AlphaRiskMarket | None = None
    calibration_summary: CalibrationSummary | None = None
