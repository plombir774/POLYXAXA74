from datetime import UTC, datetime, timedelta

from app.db.repository import MarketRepository
from app.opportunity.history import (
    OUTCOME_NO,
    OUTCOME_UNKNOWN,
    OUTCOME_YES,
    OpportunityHistoryRepository,
)
from app.opportunity.scoring import build_opportunity_candidate
from app.polymarket.schemas import MarketData


def market(slug: str, title: str = "Will Bitcoin hit $150k?") -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=title,
        url=f"https://polymarket.com/event/{slug}",
        description="Clear market.",
        yes_price=0.40,
        no_price=0.60,
        volume=1_000_000,
        volume_24hr=150_000,
        liquidity=200_000,
        spread=0.01,
        end_date=datetime.now(UTC) + timedelta(days=30),
        active=True,
        raw={},
    )


def test_prediction_storage_and_duplicate_same_day(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    history = OpportunityHistoryRepository(repository.database_path)
    candidate = build_opportunity_candidate(market("btc"))
    now = datetime(2026, 6, 18, 12, tzinfo=UTC)

    assert history.record_prediction(candidate, predicted_at=now) is True
    assert history.record_prediction(candidate, predicted_at=now.replace(hour=18)) is False

    records = history.list_records()
    assert len(records) == 1
    assert records[0].market_id == "btc"
    assert records[0].actual_outcome == OUTCOME_UNKNOWN
    assert records[0].market_probability == 0.40
    assert records[0].fair_probability_mid > 0.40


def test_outcome_update_and_metrics(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    history = OpportunityHistoryRepository(repository.database_path)
    candidate = build_opportunity_candidate(market("btc"))

    history.record_prediction(candidate, predicted_at=datetime(2026, 6, 18, 12, tzinfo=UTC))
    record = history.list_records()[0]

    assert history.update_outcome(record.id, OUTCOME_YES) is True

    updated = history.list_records()[0]
    assert updated.actual_outcome == OUTCOME_YES
    assert updated.resolution_timestamp is not None

    summary = history.metrics()
    assert summary.prediction_count == 1
    assert summary.resolved_count == 1
    assert summary.overall_accuracy == 1.0
    assert summary.by_category["crypto"].resolved_count == 1


def test_negative_outcome_counts_as_miss_for_positive_edge(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    history = OpportunityHistoryRepository(repository.database_path)
    candidate = build_opportunity_candidate(market("btc"))

    history.record_prediction(candidate, predicted_at=datetime(2026, 6, 18, 12, tzinfo=UTC))
    record = history.list_records()[0]
    history.update_outcome(record.id, OUTCOME_NO)

    summary = history.metrics()
    assert summary.overall_accuracy == 0.0
    assert summary.by_category["crypto"].brier_score > 0

