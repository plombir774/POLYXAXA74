from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class WatchedMarket:
    id: int
    market_id: str
    slug: str
    title: str
    url: str
    created_at: datetime
    is_active: bool


@dataclass(frozen=True)
class SignalRecord:
    id: int
    watched_market_id: int
    signal_score: int
    risk_score: int
    verdict: str
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class SignalHistoryRecord:
    id: int
    watched_market_id: int
    market_title: str
    signal_score: int
    risk_score: int
    verdict: str
    reason: str
    created_at: datetime
