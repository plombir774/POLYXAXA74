from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from xml.etree import ElementTree

import httpx

from app.news.models import NewsItem


logger = logging.getLogger(__name__)

NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"

RSS_FEEDS = (
    ("Reuters", "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Bloomberg", "https://feeds.bloomberg.com/markets/news.rss"),
)


class NewsProvider(Protocol):
    name: str

    async def search(
        self,
        query: str,
        limit: int,
        lookback_hours: int,
    ) -> list[NewsItem]:
        ...


class NoopNewsProvider:
    name = "none"

    async def search(
        self,
        query: str,
        limit: int,
        lookback_hours: int,
    ) -> list[NewsItem]:
        return []


class NewsAPIProvider:
    name = "newsapi"

    def __init__(self, api_key: str, timeout_seconds: float = 15.0) -> None:
        self.api_key = api_key
        self.timeout = httpx.Timeout(timeout_seconds)

    async def search(
        self,
        query: str,
        limit: int,
        lookback_hours: int,
    ) -> list[NewsItem]:
        since = datetime.now(UTC) - timedelta(hours=lookback_hours)
        params = {
            "q": query,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": max(1, min(limit, 20)),
            "from": since.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
        headers = {"X-Api-Key": self.api_key}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(NEWSAPI_ENDPOINT, params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
        articles = payload.get("articles") if isinstance(payload, dict) else []
        items = []
        for article in articles or []:
            if not isinstance(article, dict):
                continue
            title = str(article.get("title") or "").strip()
            if not title:
                continue
            source = article.get("source") if isinstance(article.get("source"), dict) else {}
            published_at = _parse_datetime(article.get("publishedAt"))
            if published_at and _older_than(published_at, lookback_hours):
                continue
            items.append(
                NewsItem(
                    title=title,
                    url=str(article.get("url") or ""),
                    source=str(source.get("name") or "NewsAPI"),
                    published_at=published_at or datetime.now(UTC),
                    summary=_clean_summary(
                        str(article.get("description") or article.get("content") or "")
                    ),
                )
            )
        return items[:limit]


class RSSProvider:
    name = "rss"

    def __init__(
        self,
        feeds: tuple[tuple[str, str], ...] = RSS_FEEDS,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.feeds = feeds
        self.timeout = httpx.Timeout(timeout_seconds)

    async def search(
        self,
        query: str,
        limit: int,
        lookback_hours: int,
    ) -> list[NewsItem]:
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            tasks = [self._fetch_feed(client, source, url) for source, url in self.feeds]
            feed_results = await asyncio.gather(*tasks, return_exceptions=True)

        candidates: list[NewsItem] = []
        for result in feed_results:
            if isinstance(result, Exception):
                logger.warning(
                    "news_rss_feed_failed error_class=%s message=%s",
                    result.__class__.__name__,
                    _short_message(result),
                )
                continue
            candidates.extend(result)

        query_tokens = _important_tokens(query)
        filtered = [
            item
            for item in candidates
            if not _older_than(item.published_at, lookback_hours)
            and _matches_query(item, query_tokens)
        ]
        filtered.sort(key=lambda item: item.published_at, reverse=True)
        return filtered[:limit]

    async def _fetch_feed(
        self,
        client: httpx.AsyncClient,
        source: str,
        url: str,
    ) -> list[NewsItem]:
        response = await client.get(url)
        response.raise_for_status()
        return _parse_rss(response.text, source)


def build_news_provider(
    provider_name: str,
    api_key: str | None,
    *,
    timeout_seconds: float = 15.0,
) -> NewsProvider:
    provider = (provider_name or "newsapi").strip().lower()
    if provider == "none":
        return NoopNewsProvider()
    if provider == "rss":
        return RSSProvider(timeout_seconds=timeout_seconds)
    if provider == "newsapi":
        if api_key:
            return NewsAPIProvider(api_key=api_key, timeout_seconds=timeout_seconds)
        return RSSProvider(timeout_seconds=timeout_seconds)
    logger.warning("news_provider_unknown provider=%s fallback=rss", provider)
    return RSSProvider(timeout_seconds=timeout_seconds)


def effective_provider_name(provider_name: str, api_key: str | None) -> str:
    provider = (provider_name or "newsapi").strip().lower()
    if provider == "none":
        return "none"
    if provider == "newsapi" and not api_key:
        return "rss"
    if provider in {"newsapi", "rss"}:
        return provider
    return "rss"


def _parse_rss(xml_text: str, fallback_source: str) -> list[NewsItem]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    items = []
    for entry in entries:
        title = _entry_text(entry, "title")
        if not title:
            continue
        url = _entry_text(entry, "link")
        if not url:
            link = entry.find("{http://www.w3.org/2005/Atom}link")
            url = link.attrib.get("href", "") if link is not None else ""
        published = (
            _entry_text(entry, "pubDate")
            or _entry_text(entry, "published")
            or _entry_text(entry, "updated")
        )
        items.append(
            NewsItem(
                title=_clean_summary(title),
                url=url,
                source=fallback_source,
                published_at=_parse_datetime(published) or datetime.now(UTC),
                summary=_clean_summary(
                    _entry_text(entry, "description")
                    or _entry_text(entry, "summary")
                    or _entry_text(entry, "content")
                ),
            )
        )
    return items


def _entry_text(entry: ElementTree.Element, tag: str) -> str:
    found = entry.find(tag)
    if found is None:
        found = entry.find(f"{{http://www.w3.org/2005/Atom}}{tag}")
    return (found.text or "").strip() if found is not None else ""


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _older_than(value: datetime, lookback_hours: int) -> bool:
    published = value if value.tzinfo else value.replace(tzinfo=UTC)
    return published < datetime.now(UTC) - timedelta(hours=lookback_hours)


def _clean_summary(value: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _important_tokens(query: str) -> set[str]:
    stop = {
        "will",
        "the",
        "and",
        "for",
        "with",
        "market",
        "news",
        "hit",
        "out",
        "president",
        "presidency",
        "politics",
    }
    return {token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 3 and token not in stop}


def _matches_query(item: NewsItem, query_tokens: set[str]) -> bool:
    if not query_tokens:
        return True
    text = f"{item.title} {item.summary}".lower()
    return bool(query_tokens.intersection(set(re.findall(r"[a-z0-9]+", text))))


def _short_message(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:240] if message else exc.__class__.__name__

