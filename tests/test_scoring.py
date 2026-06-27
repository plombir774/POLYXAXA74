from datetime import UTC, datetime, timedelta

from app.analysis.classification import MarketType, classify_market
from app.analysis.scoring import calculate_signal_score, determine_verdict
from app.polymarket.schemas import MarketData


def market(**overrides):
    data = {
        "market_id": "1",
        "slug": "test-market",
        "title": "Will the test market resolve yes?",
        "url": "https://polymarket.com/event/test-market",
        "description": "A clear test market.",
        "yes_price": 0.55,
        "no_price": 0.45,
        "volume": 120_000,
        "volume_24hr": 30_000,
        "liquidity": 60_000,
        "spread": 0.02,
        "end_date": datetime.now(UTC) + timedelta(days=14),
        "start_date": datetime.now(UTC) - timedelta(days=2),
        "raw": {},
    }
    data.update(overrides)
    return MarketData(**data)


def test_signal_score_increases_with_price_and_volume_movement() -> None:
    previous = market(yes_price=0.48, volume=40_000, volume_24hr=10_000)
    current = market(yes_price=0.56, volume=120_000, volume_24hr=30_000)
    score = calculate_signal_score(current, previous)
    assert score.components["price_movement"] == 18
    assert score.components["volume_spike"] == 20
    assert score.total >= 80


def test_signal_score_handles_missing_previous_snapshot() -> None:
    score = calculate_signal_score(market(), None)
    assert score.components["price_movement"] == 8
    assert 0 <= score.total <= 100


def test_verdict_rules() -> None:
    assert determine_verdict(85, 40) == "STRONG SIGNAL"
    assert determine_verdict(70, 60) == "WATCH"
    assert determine_verdict(90, 80) == "AVOID"
    assert determine_verdict(40, 40) == "LOW PRIORITY"


def test_meme_market_penalty_reduces_score() -> None:
    normal = market(title="Will the Fed cut rates in July?", slug="fed-cut-rates-july")
    meme = market(
        title="Will Rihanna release an album before GTA VI?",
        slug="rihanna-album-before-gta-vi",
    )

    normal_score = calculate_signal_score(normal, None)
    meme_score = calculate_signal_score(meme, None)

    assert meme_score.components["meme_penalty"] < 0
    assert meme_score.total < normal_score.total


def test_lottery_market_penalty_reduces_score() -> None:
    current = market(yes_price=0.02, no_price=0.98)
    score = calculate_signal_score(current, None)
    assert score.components["extreme_probability_penalty"] < 0
    assert score.total < 50


def test_v2_verdict_lottery_style_for_extreme_probability_near_deadline() -> None:
    """V2.3 behavior: BTC-$150k-style market with $20M volume + $206K liquidity
    + tight spread is NOT a lottery ticket — it is a serious tail-risk contract.
    It should still get EXTREME_PROBABILITY (yes price < 3%), but not LOTTERY,
    and the verdict should not be LOTTERY STYLE.
    """
    current = market(
        title="Will Bitcoin hit $150k by June 30, 2026?",
        slug="bitcoin-150k-june-30-2026",
        yes_price=0.004,
        no_price=0.996,
        volume=20_000_000,
        liquidity=206_000,
        spread=0.005,
        end_date=datetime.now(UTC) + timedelta(hours=8),
    )
    signal = calculate_signal_score(current, None)
    classification = classify_market(current, signal_score=signal.total)

    assert MarketType.LOTTERY not in classification.labels
    assert MarketType.EXTREME_PROBABILITY in classification.labels
    assert determine_verdict(signal.total, 45, classification.labels) != "LOTTERY STYLE"


def test_v2_thin_low_volume_extreme_probability_market_is_still_lottery() -> None:
    """The lottery label still applies to genuinely thin tail-bet markets."""
    current = market(
        title="Will a UFO land on the White House lawn before 2027?",
        slug="ufo-white-house-2027",
        yes_price=0.005,
        no_price=0.995,
        volume=80_000,
        liquidity=2_000,
        spread=0.08,
    )
    signal = calculate_signal_score(current, None)
    classification = classify_market(current, signal_score=signal.total)

    assert MarketType.LOTTERY in classification.labels
    assert determine_verdict(signal.total, 60, classification.labels) == "LOTTERY STYLE"


def test_v2_verdict_high_volume_no_edge_for_meme_high_volume_tight_spread() -> None:
    current = market(
        title="Will Jesus Christ return before GTA VI?",
        slug="jesus-christ-return-before-gta-vi",
        volume=12_000_000,
        liquidity=300_000,
        spread=0.01,
    )
    signal = calculate_signal_score(current, None)
    classification = classify_market(current, signal_score=signal.total)

    assert determine_verdict(signal.total, 50, classification.labels) == "HIGH VOLUME / NO EDGE"
