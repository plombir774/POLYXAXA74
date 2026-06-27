from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


class MarketData(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    market_id: str
    slug: str
    title: str
    url: str
    description: str | None = None
    yes_price: float | None = None
    no_price: float | None = None
    volume: float | None = None
    volume_24hr: float | None = None
    liquidity: float | None = None
    spread: float | None = None
    end_date: datetime | None = None
    start_date: datetime | None = None
    updated_at: datetime | None = None
    active: bool = True
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("yes_price", "no_price", "volume", "volume_24hr", "liquidity", "spread")
    @classmethod
    def normalize_float(cls, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @field_validator("end_date", "start_date", "updated_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: Any) -> datetime | None:
        return _parse_datetime(value)


class ScoreBreakdown(BaseModel):
    total: int
    components: dict[str, int]
    reason: str


class AIForecast(BaseModel):
    fair_probability_range: str
    summary: str
    why_interesting: list[str]
    risks: list[str]
    verdict: str
    confidence: str

