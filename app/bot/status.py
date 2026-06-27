from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Settings
from app.db.repository import MarketRepository
from app.news.providers import effective_provider_name
from app.opportunity.history import OpportunityHistoryRepository


LAST_SCHEDULED_CHECK_KEY = "last_scheduled_check_time"
POLYMARKET_API_STATUS_KEY = "polymarket_api_last_status"
LAST_EDGE_SCAN_KEY = "last_edge_scan_time"
EDGE_MARKETS_SCANNED_KEY = "edge_markets_scanned"
EDGE_QUALIFIED_COUNT_KEY = "edge_qualified_count"
LAST_ALPHA_RUN_KEY = "last_alpha_run_time"


@dataclass(frozen=True)
class BotStatusData:
    running: bool
    database_connected: bool
    watchlist_count: int
    last_scheduled_check_time: datetime | None
    openai_configured: bool
    news_provider: str
    news_scanner_configured: bool
    news_lookback_hours: int
    news_max_results: int
    polymarket_api_last_status: str
    check_interval_minutes: int
    min_signal_score: int
    opportunity_engine_enabled: bool
    last_edge_scan_time: datetime | None
    edge_markets_scanned: int
    edge_qualified_count: int
    alpha_engine_enabled: bool
    last_alpha_run_time: datetime | None
    predictions_tracked: int
    resolved_predictions: int
    overall_accuracy: float
    best_category: str | None


def build_status_data(
    settings: Settings,
    repository: MarketRepository,
    bot_data: dict[str, Any],
) -> BotStatusData:
    database_connected = repository.ping()
    watchlist_count = repository.watch_count() if database_connected else 0
    last_check = bot_data.get(LAST_SCHEDULED_CHECK_KEY)
    if not isinstance(last_check, datetime):
        last_check = None
    last_status = str(bot_data.get(POLYMARKET_API_STATUS_KEY) or "unknown")
    news_client = bot_data.get("news_client")
    news_provider = str(
        getattr(
            news_client,
            "provider",
            effective_provider_name(settings.news_provider, settings.news_api_key),
        )
    )
    news_configured = bool(getattr(news_client, "configured", news_provider != "none"))
    last_edge_scan = bot_data.get(LAST_EDGE_SCAN_KEY)
    if not isinstance(last_edge_scan, datetime):
        last_edge_scan = None
    last_alpha_run = bot_data.get(LAST_ALPHA_RUN_KEY)
    if not isinstance(last_alpha_run, datetime):
        last_alpha_run = None
    calibration = OpportunityHistoryRepository(repository.database_path).metrics()
    return BotStatusData(
        running=True,
        database_connected=database_connected,
        watchlist_count=watchlist_count,
        last_scheduled_check_time=last_check,
        openai_configured=bool(settings.openai_api_key),
        news_provider=news_provider,
        news_scanner_configured=news_configured,
        news_lookback_hours=settings.news_lookback_hours,
        news_max_results=settings.news_max_results,
        polymarket_api_last_status=last_status,
        check_interval_minutes=settings.check_interval_minutes,
        min_signal_score=settings.min_signal_score,
        opportunity_engine_enabled=True,
        last_edge_scan_time=last_edge_scan,
        edge_markets_scanned=int(bot_data.get(EDGE_MARKETS_SCANNED_KEY) or 0),
        edge_qualified_count=int(bot_data.get(EDGE_QUALIFIED_COUNT_KEY) or 0),
        alpha_engine_enabled=True,
        last_alpha_run_time=last_alpha_run,
        predictions_tracked=calibration.prediction_count,
        resolved_predictions=calibration.resolved_count,
        overall_accuracy=calibration.overall_accuracy,
        best_category=calibration.best_category,
    )
