from datetime import UTC, datetime, timedelta

from app.analysis.classification import MarketType, classify_market
from app.polymarket.schemas import MarketData


def market(**overrides) -> MarketData:
    data = {
        "market_id": "1",
        "slug": "test-market",
        "title": "Will the Fed cut rates in July?",
        "url": "https://polymarket.com/event/test-market",
        "description": "Clear resolution based on Fed decision.",
        "yes_price": 0.45,
        "no_price": 0.55,
        "volume": 250_000,
        "liquidity": 75_000,
        "spread": 0.02,
        "end_date": datetime.now(UTC) + timedelta(days=30),
        "raw": {},
    }
    data.update(overrides)
    return MarketData(**data)


def test_normal_market_classification() -> None:
    classification = classify_market(market())
    assert classification.primary_type in {MarketType.NORMAL, MarketType.NEWS_DRIVEN}


def test_meme_market_detection() -> None:
    classification = classify_market(
        market(
            title="Will Rihanna release a new album before GTA VI?",
            slug="new-rihanna-album-before-gta-vi",
            description="Entertainment market.",
        )
    )
    assert classification.has(MarketType.MEME)


def test_extreme_probability_detection() -> None:
    classification = classify_market(market(yes_price=0.02, no_price=0.98))
    assert classification.has(MarketType.EXTREME_PROBABILITY)
    assert classification.has(MarketType.LOTTERY)


def test_low_liquidity_and_ambiguous_resolution_detection() -> None:
    classification = classify_market(
        market(
            title="Will a major controversy happen?",
            description="Resolves if a significant controversy occurs.",
            liquidity=1_000,
        )
    )
    assert classification.has(MarketType.LOW_LIQUIDITY)
    assert classification.has(MarketType.AMBIGUOUS_RESOLUTION)

