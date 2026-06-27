from datetime import UTC, datetime, timedelta

from app.analysis.risk import calculate_risk_score
from app.polymarket.schemas import MarketData


def market(**overrides):
    data = {
        "market_id": "1",
        "slug": "test-market",
        "title": "Will the test market resolve yes?",
        "url": "https://polymarket.com/event/test-market",
        "description": "Clear market rules.",
        "yes_price": 0.5,
        "no_price": 0.5,
        "volume": 100_000,
        "liquidity": 80_000,
        "spread": 0.01,
        "end_date": datetime.now(UTC) + timedelta(days=20),
        "raw": {},
    }
    data.update(overrides)
    return MarketData(**data)


def test_low_risk_market_scores_low() -> None:
    risk = calculate_risk_score(market())
    assert risk.total <= 10


def test_missing_data_and_wide_spread_raise_risk() -> None:
    risk = calculate_risk_score(
        market(
            yes_price=None,
            liquidity=500,
            spread=0.20,
            end_date=datetime.now(UTC) + timedelta(hours=2),
            description="",
        )
    )
    assert risk.total >= 65
    assert risk.components["low_liquidity"] == 20
    assert risk.components["wide_spread"] == 20

