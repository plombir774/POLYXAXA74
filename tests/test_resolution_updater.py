from datetime import UTC, datetime, timedelta

import pytest

from app.db.repository import MarketRepository
from app.opportunity.history import OUTCOME_YES, OpportunityHistoryRepository, OpportunityResolutionUpdater
from app.opportunity.scoring import build_opportunity_candidate
from app.polymarket.schemas import MarketData


def market(slug: str, *, resolved: bool = False) -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title="Will Bitcoin hit $150k?",
        url=f"https://polymarket.com/event/{slug}",
        yes_price=1.0 if resolved else 0.40,
        no_price=0.0 if resolved else 0.60,
        volume=1_000_000,
        volume_24hr=150_000,
        liquidity=200_000,
        spread=0.01,
        end_date=datetime.now(UTC) + timedelta(days=30),
        active=not resolved,
        raw={"closed": resolved},
    )


@pytest.mark.asyncio
async def test_resolution_updater_stores_actual_outcome(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    history = OpportunityHistoryRepository(repository.database_path)
    candidate = build_opportunity_candidate(market("btc"))
    history.record_prediction(candidate)

    async def resolver(record):
        return market(record.market_id, resolved=True)

    updater = OpportunityResolutionUpdater(history, resolver)
    updated = await updater.update_resolutions()

    assert updated == 1
    record = history.list_records()[0]
    assert record.actual_outcome == OUTCOME_YES
    assert record.resolution_timestamp is not None

