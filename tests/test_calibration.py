from datetime import UTC, datetime

from app.news.scanner import calculate_relevance
from app.opportunity.history import (
    CalibrationMetrics,
    OpportunityHistoryRecord,
    apply_confidence_calibration,
    calculate_calibration_summary,
    calibration_factor,
)
from app.opportunity.scoring import build_opportunity_candidate
from app.polymarket.schemas import MarketData


def record(
    category: str,
    outcome: str,
    fair: float,
    market: float = 0.40,
) -> OpportunityHistoryRecord:
    return OpportunityHistoryRecord(
        id=1,
        market_id=f"{category}-{outcome}",
        question="Test",
        category=category,
        prediction_timestamp=datetime(2026, 6, 18, tzinfo=UTC),
        market_probability=market,
        fair_probability_mid=fair,
        edge_estimate=fair - market,
        quality_score=80,
        confidence_score=70,
        risk_score=35,
        resolution_timestamp=datetime(2026, 6, 19, tzinfo=UTC),
        actual_outcome=outcome,
    )


def market_data() -> MarketData:
    return MarketData(
        market_id="btc",
        slug="btc",
        title="Will Bitcoin hit $150k?",
        url="https://polymarket.com/event/btc",
        yes_price=0.40,
        no_price=0.60,
        volume=5_000_000,
        volume_24hr=500_000,
        liquidity=500_000,
        spread=0.01,
        raw={},
    )


def test_accuracy_and_category_reliability() -> None:
    summary = calculate_calibration_summary(
        [
            record("sports", "YES", 0.65),
            record("sports", "YES", 0.60),
            record("crypto", "NO", 0.70),
            record("crypto", "NO", 0.75),
        ]
    )

    assert summary.prediction_count == 4
    assert summary.resolved_count == 4
    assert summary.by_category["sports"].win_rate == 1.0
    assert summary.by_category["crypto"].win_rate == 0.0
    assert summary.by_category["sports"].category_reliability > summary.by_category["crypto"].category_reliability
    assert summary.best_category == "sports"
    assert summary.worst_category == "crypto"


def test_edge_adjustment_uses_calibration_factor() -> None:
    metrics = CalibrationMetrics(
        category="crypto",
        prediction_count=10,
        resolved_count=10,
        win_rate=0.25,
        average_edge=0.08,
        average_absolute_error=0.50,
        brier_score=0.45,
        calibration_error=0.40,
        category_reliability=35,
    )
    baseline = build_opportunity_candidate(market_data())
    calibrated = build_opportunity_candidate(market_data(), calibration_metrics={"crypto": metrics})

    assert calibration_factor(metrics) < 1
    assert calibrated.fair_probability_max < baseline.fair_probability_max


def test_confidence_calibration_applies_ceiling() -> None:
    assert apply_confidence_calibration(100, category_reliability=90, quality_score=90) == 90
    assert apply_confidence_calibration(100, category_reliability=75, quality_score=70) == 85
    assert apply_confidence_calibration(100, category_reliability=60, quality_score=80) == 70
    assert apply_confidence_calibration(100, category_reliability=40, quality_score=80) == 55


def test_catalyst_relevance_filters_unrelated_country_article() -> None:
    norway_score = calculate_relevance(
        "Will Norway win the 2026 FIFA World Cup?",
        "Haaland injury concern before World Cup qualifier",
        "Norwegian striker Erling Haaland misses training.",
    )
    spain_score = calculate_relevance(
        "Will Spain win the 2026 FIFA World Cup?",
        "Haaland injury concern before World Cup qualifier",
        "Norwegian striker Erling Haaland misses training.",
    )

    assert norway_score >= 45
    assert spain_score < 45

