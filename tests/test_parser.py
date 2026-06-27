import pytest

from app.polymarket.parser import MarketParseError, parse_market_input, parse_market_slug


def test_parse_plain_slug() -> None:
    assert parse_market_slug("fed-decision-in-october") == "fed-decision-in-october"


def test_parse_event_url() -> None:
    url = "https://polymarket.com/event/fed-decision-in-october?tid=123"
    assert parse_market_slug(url) == "fed-decision-in-october"


def test_parse_event_url_type() -> None:
    parsed = parse_market_input("https://polymarket.com/event/what-will-happen-before-gta-vi")
    assert parsed.slug == "what-will-happen-before-gta-vi"
    assert parsed.input_type == "event"


def test_parse_market_path() -> None:
    assert parse_market_slug("/event/will-it-rain-tomorrow") == "will-it-rain-tomorrow"


def test_reject_invalid_slug() -> None:
    # Empty input should still raise
    with pytest.raises(MarketParseError):
        parse_market_slug("")


def test_free_text_query_routed_to_text_query_input_type() -> None:
    # V2.3: free-text market questions no longer raise — they are routed to
    # the text-query search path so the bot can fuzzy-match them against
    # active markets.
    parsed = parse_market_input("Will Bitcoin hit $150k by June 30, 2026?")
    assert parsed.input_type == "text_query"
    assert "Bitcoin" in parsed.slug
