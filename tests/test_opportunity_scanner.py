from datetime import UTC, datetime, timedelta

from app.bot.messages import format_edge_opportunities
from app.opportunity.ranking import rank_opportunities
from app.opportunity.scanner import OpportunityScanner, filter_opportunity_markets
from app.opportunity.scoring import (
    build_opportunity_candidate,
    calculate_confidence_score,
    calculate_opportunity_score,
    calculate_quality_score,
)
from app.polymarket.schemas import MarketData


def market(
    slug: str,
    title: str,
    *,
    yes_price: float = 0.45,
    volume: float = 1_000_000,
    liquidity: float = 150_000,
    spread: float = 0.01,
) -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=title,
        url=f"https://polymarket.com/event/{slug}",
        description="A clear test market.",
        yes_price=yes_price,
        no_price=1 - yes_price,
        volume=volume,
        volume_24hr=100_000,
        liquidity=liquidity,
        spread=spread,
        end_date=datetime.now(UTC) + timedelta(days=30),
        active=True,
        raw={},
    )


def test_opportunity_filtering_rejects_meme_low_liquidity_duplicates_and_wide_spread() -> None:
    good = market("btc", "Will Bitcoin hit $150k by June 30, 2026?")
    duplicate = market("btc", "Will Bitcoin hit $150k by June 30, 2026?")
    meme = market("gta", "Will this happen before GTA VI?")
    low_liquidity = market("thin", "Will Fed cut rates?", liquidity=1_000)
    wide_spread = market("wide", "Will Trump win the 2028 election?", spread=0.30)

    filtered = filter_opportunity_markets([good, duplicate, meme, low_liquidity, wide_spread])

    assert filtered == [good]


def test_score_calculation_rewards_quality_markets() -> None:
    item = market("btc", "Will Bitcoin hit $150k by June 30, 2026?")

    quality = calculate_quality_score(item)
    confidence = calculate_confidence_score(item)
    opportunity = calculate_opportunity_score(7, quality, confidence, 35)

    assert quality >= 70
    assert confidence >= 70
    assert opportunity >= 60


def test_edge_estimation_builds_fair_probability_band_above_market() -> None:
    item = market("fed", "Will Fed cut rates in July?", yes_price=0.54)
    previous = market("fed", "Will Fed cut rates in July?", yes_price=0.49)

    candidate = build_opportunity_candidate(item, previous)

    assert candidate.fair_probability_min is not None
    assert candidate.fair_probability_max is not None
    assert candidate.fair_probability_min > item.yes_price
    assert "+" in candidate.edge_estimate


def test_ranking_sorts_by_opportunity_score() -> None:
    weaker = build_opportunity_candidate(
        market("weaker", "Will Fed cut rates in July?", volume=100_000, liquidity=50_000)
    )
    stronger = build_opportunity_candidate(
        market("stronger", "Will Bitcoin hit $150k by June 30, 2026?", volume=5_000_000, liquidity=500_000)
    )

    ranked = rank_opportunities([weaker, stronger])

    assert ranked[0].opportunity_score >= ranked[1].opportunity_score


def test_category_filtering() -> None:
    markets = [
        market("btc", "Will Bitcoin hit $150k by June 30, 2026?"),
        market("fed", "Will Fed cut rates in July?"),
        market("trump", "Will Trump win the 2028 election?"),
        market("fifa", "Will USA win the 2026 FIFA World Cup?"),
    ]

    assert [item.slug for item in filter_opportunity_markets(markets, "crypto")] == ["btc"]
    assert [item.slug for item in filter_opportunity_markets(markets, "macro")] == ["fed"]
    assert [item.slug for item in filter_opportunity_markets(markets, "politics")] == ["trump"]
    assert [item.slug for item in filter_opportunity_markets(markets, "sports")] == ["fifa"]


def test_scanner_empty_result_handling() -> None:
    result = OpportunityScanner().scan(
        [market("gta", "Will this happen before GTA VI?")],
        category="all",
    )

    assert result.candidates == []
    assert result.markets_scanned == 1
    assert result.qualified_count == 0
    assert "No qualified opportunities found" in format_edge_opportunities(result.candidates)

