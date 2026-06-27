from app.analysis.classification import MarketType
from app.analysis.filtering import (
    UNKNOWN_TOP_CATEGORY_MESSAGE,
    filter_top_markets,
    has_explicit_political_context,
    is_sports_market,
    parse_top_category,
)
from app.bot.messages import format_top_markets
from app.bot.top import build_top_response, render_top_response
from app.polymarket.schemas import MarketData
import pytest


def market(
    slug: str,
    title: str,
    volume: float,
    *,
    description: str = "A market.",
    raw: dict | None = None,
) -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=title,
        url=f"https://polymarket.com/event/{slug}",
        description=description,
        yes_price=0.5,
        no_price=0.5,
        volume=volume,
        liquidity=50_000,
        spread=0.02,
        raw=raw or {},
    )


def mixed_markets() -> list[MarketData]:
    return [
        market("btc-150k", "Will Bitcoin hit $150k by June 30, 2026?", 20_000_000),
        market("election", "Will Trump win the 2028 election?", 5_000_000),
        market("fed", "Will the Fed cut interest rates after CPI?", 4_000_000),
        market("nba", "Will the Lakers win the NBA Finals?", 3_000_000),
        market("gta", "New Rihanna Album before GTA VI?", 100_000),
    ]


def fifa_markets() -> list[MarketData]:
    return [
        market("uzbekistan-world-cup", "Will Uzbekistan win the 2026 FIFA World Cup?", 50_000_000),
        market("usa-world-cup", "Will USA win the 2026 FIFA World Cup?", 45_000_000),
        market("australia-world-cup", "Will Australia win the 2026 FIFA World Cup?", 40_000_000),
        market("france-world-cup", "Will France win the 2026 FIFA World Cup?", 35_000_000),
    ]


def test_top_default_excludes_obvious_meme_markets() -> None:
    rows = filter_top_markets(
        [
            market("gta", "Will this happen before GTA VI?", 100_000),
            market("fed", "Will the Fed cut rates in July?", 80_000),
        ],
        "default",
    )

    titles = [row[0].title for row in rows]
    assert "Will this happen before GTA VI?" not in titles
    assert "Will the Fed cut rates in July?" in titles


def test_top_crypto_excludes_huge_fifa_world_cup_markets() -> None:
    rows = filter_top_markets(
        [
            *fifa_markets(),
            market("bitcoin", "Will Bitcoin hit $150k?", 1_000_000),
        ],
        "crypto",
        limit=10,
    )
    assert [row[0].slug for row in rows] == ["bitcoin"]


def test_top_politics_excludes_fifa_world_cup_country_names() -> None:
    rows = filter_top_markets(
        [
            *fifa_markets(),
            market("trump-election", "Will Trump win the election?", 1_000_000),
        ],
        "politics",
        limit=10,
    )
    assert [row[0].slug for row in rows] == ["trump-election"]


def test_top_macro_excludes_fifa_world_cup_markets() -> None:
    rows = filter_top_markets(
        [
            *fifa_markets(),
            market("fed-rates", "Will Fed cut rates?", 1_000_000),
        ],
        "macro",
        limit=10,
    )
    assert [row[0].slug for row in rows] == ["fed-rates"]


def test_top_sports_includes_fifa_world_cup_markets() -> None:
    rows = filter_top_markets(fifa_markets(), "sports", limit=10)
    assert len(rows) == 4
    assert all("FIFA World Cup" in row[0].title for row in rows)


def test_explicit_category_with_no_matches_returns_empty_rows_not_unfiltered() -> None:
    rows = filter_top_markets(fifa_markets(), "crypto", limit=10)
    assert rows == []
    message = format_top_markets(rows, "crypto")
    assert "No active crypto markets found in the current top results." in message
    assert "Will USA win" not in message


def test_build_top_response_crypto_only_fifa_returns_no_match_message() -> None:
    response = build_top_response(
        "crypto",
        fifa_markets(),
        meme_allow_volume_threshold=10_000_000,
    )
    assert response.rows == []
    assert response.filtered_count == 0
    assert response.fallback_used is False
    assert "No active crypto markets found in the current top results." in response.message
    assert "FIFA World Cup" not in response.message


def test_render_top_response_crypto_contains_crypto_and_no_fifa() -> None:
    message = render_top_response(
        "crypto",
        [
            *fifa_markets(),
            market("bitcoin", "Will Bitcoin hit $150k by June 30, 2026?", 2_000_000),
            market("ethereum", "Will Ethereum hit $10k in 2026?", 1_000_000),
        ],
        meme_allow_volume_threshold=10_000_000,
    )
    assert "Bitcoin" in message
    assert "Ethereum" in message
    assert "FIFA World Cup" not in message


def test_render_top_response_macro_contains_fed_and_no_fifa() -> None:
    message = render_top_response(
        "macro",
        [*fifa_markets(), market("fed", "Will Fed cut rates in July?", 1_000_000)],
        meme_allow_volume_threshold=10_000_000,
    )
    assert "Fed cut rates" in message
    assert "FIFA World Cup" not in message


def test_render_top_response_politics_contains_trump_and_no_fifa() -> None:
    message = render_top_response(
        "politics",
        [*fifa_markets(), market("trump", "Will Trump win the 2028 election?", 1_000_000)],
        meme_allow_volume_threshold=10_000_000,
    )
    assert "Trump" in message
    assert "FIFA World Cup" not in message


def test_render_top_response_sports_contains_fifa_and_esports() -> None:
    message = render_top_response(
        "sports",
        [
            *fifa_markets(),
            market("esports", "Will Global Esports win Masters London?", 1_000_000),
        ],
        meme_allow_volume_threshold=10_000_000,
    )
    assert "FIFA World Cup" in message
    assert "Global Esports" in message


def test_render_top_response_crypto_only_fifa_has_empty_message() -> None:
    message = render_top_response(
        "crypto",
        fifa_markets(),
        meme_allow_volume_threshold=10_000_000,
    )
    assert "No active crypto markets found in the current top results." in message
    assert "FIFA World Cup" not in message


def test_build_top_response_politics_only_fifa_returns_no_match_message() -> None:
    response = build_top_response(
        "politics",
        fifa_markets(),
        meme_allow_volume_threshold=10_000_000,
    )
    assert response.rows == []
    assert response.filtered_count == 0
    assert response.fallback_used is False
    assert "No active politics markets found in the current top results." in response.message
    assert "FIFA World Cup" not in response.message


def test_build_top_response_macro_only_fifa_returns_no_match_message() -> None:
    response = build_top_response(
        "macro",
        fifa_markets(),
        meme_allow_volume_threshold=10_000_000,
    )
    assert response.rows == []
    assert response.filtered_count == 0
    assert response.fallback_used is False
    assert "No active macro markets found in the current top results." in response.message
    assert "FIFA World Cup" not in response.message


def test_is_sports_market_hard_exclusion_and_political_override() -> None:
    assert is_sports_market("Will USA win the 2026 FIFA World Cup?", {}, "")
    assert not has_explicit_political_context("Will USA win the 2026 FIFA World Cup?", {}, "")
    assert has_explicit_political_context(
        "Will a government boycott the 2026 FIFA World Cup?",
        {},
        "",
    )


def test_country_name_alone_does_not_make_market_politics() -> None:
    rows = filter_top_markets(
        [market("france-world-cup", "Will France win the 2026 FIFA World Cup?", 35_000_000)],
        "politics",
        limit=10,
    )
    assert rows == []


def test_usa_world_cup_is_sports_not_politics() -> None:
    item = market("usa-world-cup", "Will USA win the 2026 FIFA World Cup?", 45_000_000)
    assert filter_top_markets([item], "sports", limit=10)
    assert filter_top_markets([item], "politics", limit=10) == []


def test_bitcoin_market_is_crypto_not_sports_or_macro() -> None:
    item = market("bitcoin", "Will Bitcoin hit $150k?", 20_000_000)
    assert filter_top_markets([item], "crypto", limit=10)
    assert filter_top_markets([item], "sports", limit=10) == []
    assert filter_top_markets([item], "macro", limit=10) == []


def test_fed_market_is_macro() -> None:
    assert filter_top_markets([market("fed", "Will Fed cut rates?", 1_000_000)], "macro", limit=10)


def test_trump_election_market_is_politics() -> None:
    assert filter_top_markets(
        [market("trump", "Will Trump win election?", 1_000_000)],
        "politics",
        limit=10,
    )


def test_top_all_keeps_meme_markets() -> None:
    rows = filter_top_markets(
        [market("gta", "Will this happen before GTA VI?", 100_000)],
        "all",
    )
    assert rows[0][3].has(MarketType.MEME)


def test_top_crypto_returns_crypto_markets_only() -> None:
    rows = filter_top_markets(mixed_markets(), "crypto", limit=10)
    assert [row[0].slug for row in rows] == ["btc-150k"]


def test_top_politics_returns_politics_markets_only() -> None:
    rows = filter_top_markets(mixed_markets(), "politics", limit=10)
    assert [row[0].slug for row in rows] == ["election"]


def test_top_macro_returns_macro_markets_only() -> None:
    rows = filter_top_markets(mixed_markets(), "macro", limit=10)
    assert [row[0].slug for row in rows] == ["fed"]


def test_top_sports_returns_sports_markets_only() -> None:
    rows = filter_top_markets(mixed_markets(), "sports", limit=10)
    assert [row[0].slug for row in rows] == ["nba"]


def test_top_message_includes_market_type_verdict_and_reason() -> None:
    rows = filter_top_markets([market("gta", "Will this happen before GTA VI?", 20_000_000)], "all")
    message = format_top_markets(rows, "all")
    assert "Type:" in message
    assert "Verdict:" in message
    assert "Why:" in message


def test_parse_top_category_variants() -> None:
    assert parse_top_category([]) == "default"
    assert parse_top_category(["all"]) == "all"
    assert parse_top_category(["crypto"]) == "crypto"
    assert parse_top_category(["politics"]) == "politics"
    assert parse_top_category(["macro"]) == "macro"
    assert parse_top_category(["sports"]) == "sports"


def test_unknown_top_category_returns_friendly_help_text() -> None:
    with pytest.raises(ValueError) as exc:
        parse_top_category(["celebrities"])
    assert str(exc.value) == UNKNOWN_TOP_CATEGORY_MESSAGE
