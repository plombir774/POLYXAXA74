from __future__ import annotations

from collections.abc import Sequence

from app.news.models import NewsItem


BULLISH_KEYWORDS = (
    "approval",
    "approved",
    "growth",
    "record",
    "breakthrough",
    "surge",
    "surges",
    "wins",
    "expands",
    "rally",
    "inflows",
    "optimism",
    "lead",
    "qualifies",
)

BEARISH_KEYWORDS = (
    "ban",
    "lawsuit",
    "decline",
    "collapse",
    "crash",
    "recession",
    "war",
    "rejected",
    "sanctions",
    "investigation",
    "falls",
    "injury",
    "default",
)


def analyze_news_sentiment(news: str | Sequence[NewsItem]) -> str:
    text = _news_text(news)
    bullish = sum(text.count(keyword) for keyword in BULLISH_KEYWORDS)
    bearish = sum(text.count(keyword) for keyword in BEARISH_KEYWORDS)
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def calculate_sentiment_confidence(news: str | Sequence[NewsItem], sentiment: str) -> int:
    if sentiment == "neutral":
        return 35 if _news_text(news).strip() else 0
    text = _news_text(news)
    directional = BULLISH_KEYWORDS if sentiment == "bullish" else BEARISH_KEYWORDS
    opposite = BEARISH_KEYWORDS if sentiment == "bullish" else BULLISH_KEYWORDS
    directional_hits = sum(text.count(keyword) for keyword in directional)
    opposite_hits = sum(text.count(keyword) for keyword in opposite)
    return max(0, min(100, 45 + directional_hits * 12 - opposite_hits * 8))


def _news_text(news: str | Sequence[NewsItem]) -> str:
    if isinstance(news, str):
        return news.lower()
    return " ".join(f"{item.title} {item.summary}" for item in news).lower()

