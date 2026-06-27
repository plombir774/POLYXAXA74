from datetime import UTC, datetime

import pytest

from app.news.models import NewsItem
from app.news.providers import NewsAPIProvider, RSSProvider, build_news_provider
from app.news.scanner import scan_market_news


def test_missing_newsapi_key_falls_back_to_rss_provider() -> None:
    provider = build_news_provider("newsapi", None)
    assert isinstance(provider, RSSProvider)
    assert provider.name == "rss"


def test_newsapi_key_uses_newsapi_provider() -> None:
    provider = build_news_provider("newsapi", "test-key")
    assert isinstance(provider, NewsAPIProvider)
    assert provider.name == "newsapi"


class FailingProvider:
    name = "failing"

    async def search(self, query: str, limit: int, lookback_hours: int) -> list[NewsItem]:
        raise RuntimeError("network unavailable")


@pytest.mark.asyncio
async def test_provider_failure_returns_empty_result() -> None:
    result = await scan_market_news(
        "Will Bitcoin hit $150k by June 30, 2026?",
        "NEWS_DRIVEN",
        provider=FailingProvider(),
    )

    assert result.news_found is False
    assert result.catalyst_score == 0
    assert result.error == "network unavailable"

