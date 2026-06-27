from datetime import UTC, datetime, timedelta

from app.db.repository import MarketRepository
from app.polymarket.schemas import MarketData


def make_market(slug: str = "test-market") -> MarketData:
    return MarketData(
        market_id="market-1",
        slug=slug,
        title="Will this test market resolve yes?",
        url=f"https://polymarket.com/event/{slug}",
        description="A test market.",
        yes_price=0.51,
        no_price=0.49,
        volume=10_000,
        liquidity=5_000,
        spread=0.03,
        end_date=datetime.now(UTC) + timedelta(days=10),
        raw={"description": "A test market.", "active": True},
    )


def test_add_list_remove_watch(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    watch = repository.add_watch(make_market())
    assert watch.id > 0
    assert repository.list_watches()[0].slug == "test-market"
    assert repository.remove_watch(watch.id) is True
    assert repository.list_watches() == []


def test_snapshot_round_trip(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    market = make_market()
    watch = repository.add_watch(market)
    snapshot_id = repository.add_snapshot(watch.id, market)
    assert snapshot_id > 0
    latest = repository.get_latest_snapshot(watch.id)
    assert latest is not None
    assert latest.slug == market.slug
    assert latest.yes_price == market.yes_price


def test_readding_watch_reactivates(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    market = make_market()
    watch = repository.add_watch(market)
    repository.remove_watch(watch.id)
    readded = repository.add_watch(make_market())
    assert readded.id == watch.id
    assert readded.is_active is True


def test_recent_signal_history_includes_market_title(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    first = repository.add_watch(make_market("first-market"))
    second = repository.add_watch(make_market("second-market"))

    first_signal_id = repository.add_signal(first.id, 65, 40, "WATCH", "First signal")
    second_signal_id = repository.add_signal(second.id, 82, 35, "STRONG SIGNAL", "Second signal")

    history = repository.list_recent_signals(limit=10)

    assert [record.id for record in history] == [second_signal_id, first_signal_id]
    assert history[0].market_title == "Will this test market resolve yes?"
    assert history[0].signal_score == 82
    assert history[0].risk_score == 35
