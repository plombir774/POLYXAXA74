from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NewsSearchResult(BaseModel):
    title: str
    url: str | None = None
    source: str | None = None
    published_at: datetime | None = None
    snippet: str | None = None


class NewsSearchResponse(BaseModel):
    provider: str
    results: list[NewsSearchResult] = Field(default_factory=list)
    scanner_not_configured: bool = False
    error: str | None = None


class CatalystRelevance(BaseModel):
    score: int
    confidence: str
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    plausible_effect: str = "unclear"


class CatalystAnalysis(BaseModel):
    scanner_not_configured: bool = False
    fresh_catalyst: str = "unknown"
    possible_catalyst: str = "none detected"
    source_confidence: str = "low"
    market_reaction: str = "unclear"
    notes: list[str] = Field(default_factory=list)
    relevance_score: int = 0
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    results: list[NewsSearchResult] = Field(default_factory=list)
