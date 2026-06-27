from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Awaitable, Callable, Iterable

from app.db.database import connect
from app.opportunity.models import OpportunityCandidate
from app.polymarket.schemas import MarketData


OUTCOME_YES = "YES"
OUTCOME_NO = "NO"
OUTCOME_UNKNOWN = "UNKNOWN"
CATEGORIES = ("crypto", "politics", "sports", "macro", "other")


@dataclass(frozen=True)
class OpportunityHistoryRecord:
    id: int
    market_id: str
    question: str
    category: str
    prediction_timestamp: datetime
    market_probability: float
    fair_probability_mid: float
    edge_estimate: float
    quality_score: int
    confidence_score: int
    risk_score: int
    resolution_timestamp: datetime | None
    actual_outcome: str


@dataclass(frozen=True)
class CalibrationMetrics:
    category: str
    prediction_count: int
    resolved_count: int
    win_rate: float
    average_edge: float
    average_absolute_error: float
    brier_score: float
    calibration_error: float
    category_reliability: int


@dataclass(frozen=True)
class CalibrationSummary:
    prediction_count: int
    resolved_count: int
    overall_accuracy: float
    by_category: dict[str, CalibrationMetrics]
    best_category: str | None
    worst_category: str | None


class OpportunityHistoryRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def record_prediction(
        self,
        candidate: OpportunityCandidate,
        *,
        predicted_at: datetime | None = None,
    ) -> bool:
        if candidate.yes_price is None:
            return False
        fair_mid = _fair_mid(candidate)
        if fair_mid is None:
            return False
        predicted_at = predicted_at or datetime.now(UTC)
        if self._prediction_exists_today(candidate.market_id, predicted_at.date()):
            return False
        with connect(self.database_path) as connection:
            connection.execute(
                """
                INSERT INTO opportunity_history (
                    market_id, question, category, prediction_timestamp,
                    market_probability, fair_probability_mid, edge_estimate,
                    quality_score, confidence_score, risk_score,
                    resolution_timestamp, actual_outcome
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
                """,
                (
                    candidate.market_id,
                    candidate.question,
                    _history_category(candidate.category),
                    predicted_at.isoformat(),
                    candidate.yes_price,
                    fair_mid,
                    fair_mid - candidate.yes_price,
                    candidate.quality_score,
                    candidate.confidence_score,
                    candidate.risk_score,
                    OUTCOME_UNKNOWN,
                ),
            )
            connection.commit()
        return True

    def record_predictions(
        self,
        candidates: Iterable[OpportunityCandidate],
        *,
        predicted_at: datetime | None = None,
    ) -> int:
        count = 0
        predicted_at = predicted_at or datetime.now(UTC)
        for candidate in candidates:
            count += int(self.record_prediction(candidate, predicted_at=predicted_at))
        return count

    def unresolved_predictions(self, limit: int = 100) -> list[OpportunityHistoryRecord]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM opportunity_history
                WHERE actual_outcome = ?
                ORDER BY prediction_timestamp ASC
                LIMIT ?
                """,
                (OUTCOME_UNKNOWN, limit),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def update_outcome(
        self,
        record_id: int,
        actual_outcome: str,
        *,
        resolved_at: datetime | None = None,
    ) -> bool:
        if actual_outcome not in {OUTCOME_YES, OUTCOME_NO, OUTCOME_UNKNOWN}:
            raise ValueError("actual_outcome must be YES, NO, or UNKNOWN")
        if actual_outcome == OUTCOME_UNKNOWN:
            return False
        resolved_at = resolved_at or datetime.now(UTC)
        with connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE opportunity_history
                SET actual_outcome = ?, resolution_timestamp = ?
                WHERE id = ?
                """,
                (actual_outcome, resolved_at.isoformat(), record_id),
            )
            connection.commit()
        return cursor.rowcount > 0

    def list_records(self, limit: int = 1000) -> list[OpportunityHistoryRecord]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """
                SELECT * FROM opportunity_history
                ORDER BY prediction_timestamp DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_record_from_row(row) for row in rows]

    def metrics(self) -> CalibrationSummary:
        records = self.list_records(limit=10_000)
        return calculate_calibration_summary(records)

    def _prediction_exists_today(self, market_id: str, prediction_date: date) -> bool:
        with connect(self.database_path) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM opportunity_history
                WHERE market_id = ? AND date(prediction_timestamp) = ?
                LIMIT 1
                """,
                (market_id, prediction_date.isoformat()),
            ).fetchone()
        return row is not None


class OpportunityResolutionUpdater:
    def __init__(
        self,
        history: OpportunityHistoryRepository,
        resolver: Callable[[OpportunityHistoryRecord], Awaitable[MarketData | None]],
    ) -> None:
        self.history = history
        self.resolver = resolver

    async def update_resolutions(self, limit: int = 100) -> int:
        updated = 0
        for record in self.history.unresolved_predictions(limit=limit):
            market = await self.resolver(record)
            outcome = infer_market_outcome(market) if market else OUTCOME_UNKNOWN
            if outcome in {OUTCOME_YES, OUTCOME_NO}:
                updated += int(self.history.update_outcome(record.id, outcome))
        return updated


def calculate_calibration_summary(
    records: Iterable[OpportunityHistoryRecord],
) -> CalibrationSummary:
    all_records = list(records)
    by_category = {
        category: calculate_category_metrics(category, all_records)
        for category in CATEGORIES
    }
    resolved = [record for record in all_records if record.actual_outcome in {OUTCOME_YES, OUTCOME_NO}]
    prediction_count = len(all_records)
    resolved_count = len(resolved)
    overall_accuracy = _accuracy(resolved)
    resolved_metrics = [metric for metric in by_category.values() if metric.resolved_count > 0]
    best = max(resolved_metrics, key=lambda item: item.category_reliability).category if resolved_metrics else None
    worst = min(resolved_metrics, key=lambda item: item.category_reliability).category if resolved_metrics else None
    return CalibrationSummary(
        prediction_count=prediction_count,
        resolved_count=resolved_count,
        overall_accuracy=overall_accuracy,
        by_category=by_category,
        best_category=best,
        worst_category=worst,
    )


def calculate_category_metrics(
    category: str,
    records: Iterable[OpportunityHistoryRecord],
) -> CalibrationMetrics:
    selected = [record for record in records if _history_category(record.category) == category]
    resolved = [record for record in selected if record.actual_outcome in {OUTCOME_YES, OUTCOME_NO}]
    win_rate = _accuracy(resolved)
    average_edge = _avg(abs(record.edge_estimate) for record in selected)
    average_absolute_error = _avg(
        abs(record.fair_probability_mid - _actual_probability(record.actual_outcome))
        for record in resolved
    )
    brier_score = _avg(
        (record.fair_probability_mid - _actual_probability(record.actual_outcome)) ** 2
        for record in resolved
    )
    calibration_error = _avg(
        abs(record.market_probability - _actual_probability(record.actual_outcome))
        for record in resolved
    )
    reliability = reliability_from_metrics(
        resolved_count=len(resolved),
        win_rate=win_rate,
        average_absolute_error=average_absolute_error,
        brier_score=brier_score,
    )
    return CalibrationMetrics(
        category=category,
        prediction_count=len(selected),
        resolved_count=len(resolved),
        win_rate=win_rate,
        average_edge=average_edge,
        average_absolute_error=average_absolute_error,
        brier_score=brier_score,
        calibration_error=calibration_error,
        category_reliability=reliability,
    )


def reliability_from_metrics(
    *,
    resolved_count: int,
    win_rate: float,
    average_absolute_error: float,
    brier_score: float,
) -> int:
    if resolved_count == 0:
        return 55
    sample_factor = min(1.0, resolved_count / 25)
    score = 45 + win_rate * 35 - average_absolute_error * 20 - brier_score * 25
    score = score * (0.75 + 0.25 * sample_factor)
    return max(0, min(100, round(score)))


def calibration_factor(metrics: CalibrationMetrics | None) -> float:
    if metrics is None or metrics.resolved_count == 0:
        return 1.0
    overestimate = metrics.average_absolute_error
    if metrics.win_rate >= 0.65 and overestimate <= 0.30:
        return 1.05
    if metrics.win_rate < 0.50:
        return 0.80
    if overestimate > 0.45:
        return 0.85
    if overestimate > 0.35:
        return 0.90
    return 1.0


def calibrated_confidence_ceiling(
    category_reliability: int,
    quality_score: int,
) -> int:
    if category_reliability >= 80 and quality_score >= 80:
        return 90
    if category_reliability >= 70 and quality_score >= 65:
        return 85
    if category_reliability >= 55:
        return 70
    return 55


def apply_confidence_calibration(
    confidence_score: int,
    *,
    category_reliability: int,
    quality_score: int,
) -> int:
    ceiling = calibrated_confidence_ceiling(category_reliability, quality_score)
    return max(0, min(confidence_score, ceiling))


def infer_market_outcome(market: MarketData | None) -> str:
    if market is None:
        return OUTCOME_UNKNOWN
    raw = market.raw or {}
    outcome = raw.get("actual_outcome") or raw.get("resolvedOutcome") or raw.get("outcome")
    if isinstance(outcome, str):
        normalized = outcome.strip().upper()
        if normalized in {OUTCOME_YES, OUTCOME_NO}:
            return normalized
    winner = raw.get("winner") or raw.get("winningOutcome")
    if isinstance(winner, str):
        normalized = winner.strip().upper()
        if normalized in {OUTCOME_YES, OUTCOME_NO}:
            return normalized
    if raw.get("closed") and market.yes_price is not None:
        if market.yes_price >= 0.99:
            return OUTCOME_YES
        if market.yes_price <= 0.01:
            return OUTCOME_NO
    return OUTCOME_UNKNOWN


def _record_from_row(row: sqlite3.Row) -> OpportunityHistoryRecord:
    return OpportunityHistoryRecord(
        id=int(row["id"]),
        market_id=str(row["market_id"]),
        question=str(row["question"]),
        category=str(row["category"]),
        prediction_timestamp=_parse_datetime(row["prediction_timestamp"]),
        market_probability=float(row["market_probability"]),
        fair_probability_mid=float(row["fair_probability_mid"]),
        edge_estimate=float(row["edge_estimate"]),
        quality_score=int(row["quality_score"]),
        confidence_score=int(row["confidence_score"]),
        risk_score=int(row["risk_score"]),
        resolution_timestamp=_parse_optional_datetime(row["resolution_timestamp"]),
        actual_outcome=str(row["actual_outcome"]),
    )


def _fair_mid(candidate: OpportunityCandidate) -> float | None:
    if candidate.fair_probability_min is None or candidate.fair_probability_max is None:
        return None
    return (candidate.fair_probability_min + candidate.fair_probability_max) / 2


def _history_category(category: str) -> str:
    return category if category in CATEGORIES else "other"


def _accuracy(records: list[OpportunityHistoryRecord]) -> float:
    if not records:
        return 0.0
    wins = 0
    for record in records:
        predicted_yes = record.fair_probability_mid >= record.market_probability
        actual_yes = record.actual_outcome == OUTCOME_YES
        wins += int(predicted_yes == actual_yes)
    return wins / len(records)


def _actual_probability(outcome: str) -> float:
    return 1.0 if outcome == OUTCOME_YES else 0.0


def _avg(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(items) / len(items)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _parse_optional_datetime(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    return _parse_datetime(value)

