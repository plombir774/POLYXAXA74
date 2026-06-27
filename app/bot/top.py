from __future__ import annotations

from dataclasses import dataclass

from app.analysis.filtering import MarketAnalysisRow, filter_top_markets
from app.bot.messages import format_top_markets
from app.polymarket.schemas import MarketData


EXPLICIT_TOP_CATEGORIES = {"crypto", "politics", "macro", "sports"}


@dataclass(frozen=True)
class TopResponse:
    message: str
    rows: list[MarketAnalysisRow]
    fetched_count: int
    filtered_count: int
    first_fetched_titles: list[str]
    first_filtered_titles: list[str]
    fallback_used: bool


def top_fetch_limit(category: str) -> int:
    return 200 if category in EXPLICIT_TOP_CATEGORIES else 50


def build_top_response(
    category: str,
    markets: list[MarketData],
    *,
    meme_allow_volume_threshold: int,
    limit: int = 10,
) -> TopResponse:
    rows = filter_top_markets(
        markets,
        category,
        limit=limit,
        meme_allow_volume_threshold=meme_allow_volume_threshold,
    )
    return TopResponse(
        message=format_top_markets(rows, category),
        rows=rows,
        fetched_count=len(markets),
        filtered_count=len(rows),
        first_fetched_titles=[market.title for market in markets[:5]],
        first_filtered_titles=[row[0].title for row in rows[:5]],
        fallback_used=False,
    )


def render_top_response(
    category: str,
    markets: list[MarketData],
    *,
    meme_allow_volume_threshold: int,
    limit: int = 10,
) -> str:
    return build_top_response(
        category,
        markets,
        meme_allow_volume_threshold=meme_allow_volume_threshold,
        limit=limit,
    ).message
