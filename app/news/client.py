from __future__ import annotations

import logging

import httpx

from app.news.models import NewsItem, NewsScanResult
from app.news.providers import build_news_provider
from app.news.scanner import scan_market_news
from app.news.schemas import NewsSearchResponse, NewsSearchResult


logger = logging.getLogger(__name__)


class NewsClient:
    def __init__(
        self,
        provider: str = "newsapi",
        api_key: str | None = None,
        lookback_hours: int = 24,
        max_results: int = 5,
        timeout_seconds: float = 15.0,
        openai_api_key: str | None = None,
        openai_model: str = "gpt-5.5",
    ) -> None:
        self.requested_provider = (provider or "newsapi").strip().lower()
        self.api_key = api_key
        self.lookback_hours = lookback_hours
        self.max_results = max_results
        self.timeout = httpx.Timeout(timeout_seconds)
        self.timeout_seconds = timeout_seconds
        self.openai_api_key = openai_api_key
        self.openai_model = openai_model
        self.provider_client = build_news_provider(
            self.requested_provider,
            self.api_key,
            timeout_seconds=timeout_seconds,
        )
        self.provider = getattr(self.provider_client, "name", self.requested_provider)

    @property
    def configured(self) -> bool:
        return self.provider != "none"

    async def search(self, queries: list[str]) -> NewsSearchResponse:
        if self.provider == "none":
            return NewsSearchResponse(
                provider=self.provider,
                scanner_not_configured=True,
                error="NEWS_PROVIDER is none.",
            )
        try:
            items: list[NewsItem] = []
            for query in queries:
                items.extend(
                    await self.provider_client.search(
                        query,
                        limit=self.max_results,
                        lookback_hours=self.lookback_hours,
                    )
                )
        except Exception as exc:
            logger.warning(
                "news_client_search_failed provider=%s error_class=%s message=%s",
                self.provider,
                exc.__class__.__name__,
                _short_message(exc),
            )
            return NewsSearchResponse(provider=self.provider, error=_short_message(exc))
        return NewsSearchResponse(
            provider=self.provider,
            results=[self._to_search_result(item) for item in items[: self.max_results]],
        )

    async def scan_market(
        self,
        market_title: str,
        market_type: str,
        *,
        use_openai: bool = True,
    ) -> NewsScanResult:
        if self.provider == "none":
            return NewsScanResult(
                news_found=False,
                provider="none",
                summary="External news scanner is not configured. Using market data only.",
                error="NEWS_PROVIDER is none.",
            )
        return await scan_market_news(
            market_title=market_title,
            market_type=market_type,
            lookback_hours=self.lookback_hours,
            limit=self.max_results,
            provider=self.provider_client,
            openai_api_key=self.openai_api_key if use_openai else None,
            openai_model=self.openai_model,
            timeout_seconds=self.timeout_seconds,
        )

    async def _generic_http_stub(self, queries: list[str]) -> NewsSearchResponse:
        logger.info(
            "news_search_stub provider=generic_http_stub query_count=%s max_results=%s",
            len(queries),
            self.max_results,
        )
        return NewsSearchResponse(
            provider=self.provider,
            results=[],
            error="generic_http_stub is configured but has no external endpoint implementation.",
        )

    def normalize_results(self, items: list[dict]) -> list[NewsSearchResult]:
        results = []
        for item in items[: self.max_results]:
            title = item.get("title")
            if not title:
                continue
            results.append(
                NewsSearchResult(
                    title=str(title),
                    url=item.get("url"),
                    source=item.get("source"),
                    published_at=item.get("published_at"),
                    snippet=item.get("snippet"),
                )
            )
        return results

    def _to_search_result(self, item: NewsItem) -> NewsSearchResult:
        return NewsSearchResult(
            title=item.title,
            url=item.url,
            source=item.source,
            published_at=item.published_at,
            snippet=item.summary,
        )


def _short_message(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:240] if message else exc.__class__.__name__
