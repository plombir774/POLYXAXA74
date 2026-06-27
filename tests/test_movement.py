from datetime import UTC, datetime, timedelta

import pytest

from app.analysis.movement import calculate_snapshot_movement, format_snapshot_movement
from app.polymarket.schemas import MarketData


def market(price: float, updated_at: datetime | None = None) -> MarketData:
    return MarketData(
        market_id="1",
        slug="movement-market",
        title="Movement market",
        url="https://polymarket.com/event/movement-market",
        yes_price=price,
        no_price=1 - price,
        volume=100_000,
        liquidity=50_000,
        spread=0.02,
        updated_at=updated_at,
        raw={},
    )


def test_snapshot_movement_calculation() -> None:
    now = datetime(2026, 6, 10, 12, 0, tzinfo=UTC)
    current = market(0.60, now)
    history = [
        market(0.55, now - timedelta(hours=1, minutes=5)),
        market(0.50, now - timedelta(hours=6, minutes=5)),
        market(0.45, now - timedelta(hours=24, minutes=5)),
    ]

    movement = calculate_snapshot_movement(current, history, now=now)

    assert movement.change_1h == pytest.approx(0.05)
    assert movement.change_6h == pytest.approx(0.10)
    assert movement.change_24h == pytest.approx(0.15)
    assert "1h: +5.0%" in format_snapshot_movement(movement)


def test_snapshot_movement_without_history() -> None:
    movement = calculate_snapshot_movement(market(0.60), [], now=datetime(2026, 6, 10, tzinfo=UTC))
    assert format_snapshot_movement(movement) == "Not enough history yet."
