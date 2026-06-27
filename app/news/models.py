from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str


@dataclass(frozen=True)
class NewsScanResult:
    news_found: bool
    items: list[NewsItem] = field(default_factory=list)
    sentiment: str = "neutral"
    confidence: int = 0
    catalyst_score: int = 0
    summary: str = "No relevant external catalysts found during the selected lookback window."
    provider: str = "none"
    query: str = ""
    error: str | None = None

