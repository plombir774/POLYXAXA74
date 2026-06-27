from datetime import UTC, datetime, timedelta

import pytest

from app.news.models import NewsItem
from app.news.scanner import (
    build_market_query,
    calculate_relevance,
    deduplicate_news_items,
    scan_market_news,
)


def item(title: str, url: str, hours_ago: int = 2, summary: str = "") -> NewsItem:
    return NewsItem(
        title=title,
        url=url,
        source="Reuters",
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        summary=summary,
    )


def test_query_generation_bitcoin_taiwan_trump() -> None:
    assert build_market_query("Will Bitcoin hit $150k by June 30, 2026?") == (
        "Bitcoin BTC crypto market"
    )
    assert build_market_query("Will China invade Taiwan?") == "China Taiwan geopolitical news"
    assert build_market_query("Trump out as President?") == "Trump presidency politics"


def test_deduplicates_duplicate_titles_and_urls() -> None:
    first = item("Bitcoin ETF approval discussion", "https://example.com/a")
    same_title = item("Bitcoin ETF approval discussion", "https://example.com/b")
    same_url = item("Different Bitcoin headline", "https://example.com/a")
    unique = item("Bitcoin treasury purchases increase", "https://example.com/c")

    deduped = deduplicate_news_items([first, same_title, same_url, unique])

    assert deduped == [first, unique]


def test_relevance_uses_keyword_overlap() -> None:
    score = calculate_relevance(
        "Will Bitcoin hit $150k by June 30, 2026?",
        "Bitcoin ETF inflows surge",
        "BTC investors watch crypto market momentum.",
    )
    assert score >= 60


class FakeProvider:
    name = "fake"

    async def search(self, query: str, limit: int, lookback_hours: int) -> list[NewsItem]:
        return [
            item(
                "Bitcoin ETF approval discussion",
                "https://example.com/a",
                summary="BTC inflows surge as crypto market optimism grows.",
            ),
            item("Unrelated local weather update", "https://example.com/b", summary="Rain likely."),
        ]


@pytest.mark.asyncio
async def test_scan_market_news_returns_relevant_items_and_score() -> None:
    result = await scan_market_news(
        "Will Bitcoin hit $150k by June 30, 2026?",
        "NEWS_DRIVEN",
        provider=FakeProvider(),
        limit=5,
    )

    assert result.news_found is True
    assert result.items[0].title == "Bitcoin ETF approval discussion"
    assert result.sentiment == "bullish"
    assert result.catalyst_score > 0


@pytest.mark.asyncio
async def test_openai_summary_exception_does_not_break_scan(monkeypatch) -> None:
    async def fail_summary(**kwargs):
        raise RuntimeError("openai unavailable")

    monkeypatch.setattr("app.news.scanner._try_openai_summary", fail_summary)

    result = await scan_market_news(
        "Will Bitcoin hit $150k by June 30, 2026?",
        "NEWS_DRIVEN",
        provider=FakeProvider(),
        openai_api_key="sk-test",
    )

    assert result.news_found is True
    assert "Bitcoin ETF approval discussion" in result.summary

