from __future__ import annotations

from telegram.ext import Application, ApplicationBuilder

from app.analysis.forecast import OpenAIForecastClient
from app.bot.handlers import register_handlers
from app.bot.jobs import check_watchlist_job, opportunity_resolution_job
from app.bot.status import LAST_SCHEDULED_CHECK_KEY, POLYMARKET_API_STATUS_KEY
from app.config import Settings, load_settings, validate_runtime_settings
from app.db.repository import MarketRepository
from app.news.client import NewsClient
from app.polymarket.client import PolymarketClient
from app.utils.logging import configure_logging


def build_application(settings: Settings) -> Application:
    repository = MarketRepository(settings.database_path)
    repository.init_schema()
    polymarket_client = PolymarketClient(
        gamma_base_url=settings.polymarket_gamma_base_url,
        clob_base_url=settings.polymarket_clob_base_url,
        timeout_seconds=settings.request_timeout_seconds,
        max_retries=settings.polymarket_max_retries,
        retry_backoff_seconds=settings.polymarket_retry_backoff_seconds,
    )
    ai_forecast_client = OpenAIForecastClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
    )
    news_client = NewsClient(
        provider=settings.news_provider,
        api_key=settings.news_api_key,
        lookback_hours=settings.news_lookback_hours,
        max_results=settings.news_max_results,
        timeout_seconds=settings.request_timeout_seconds,
        openai_api_key=settings.openai_api_key,
        openai_model=settings.openai_model,
    )

    application = ApplicationBuilder().token(settings.telegram_bot_token).build()
    application.bot_data["settings"] = settings
    application.bot_data["repository"] = repository
    application.bot_data["polymarket_client"] = polymarket_client
    application.bot_data["ai_forecast_client"] = ai_forecast_client
    application.bot_data["news_client"] = news_client
    application.bot_data[LAST_SCHEDULED_CHECK_KEY] = None
    application.bot_data[POLYMARKET_API_STATUS_KEY] = "unknown"

    register_handlers(application)
    if application.job_queue:
        interval_seconds = max(60, settings.check_interval_minutes * 60)
        application.job_queue.run_repeating(
            check_watchlist_job,
            interval=interval_seconds,
            first=30,
            name="watchlist_check",
        )
        application.job_queue.run_repeating(
            opportunity_resolution_job,
            interval=24 * 60 * 60,
            first=90,
            name="opportunity_resolution_update",
        )
    return application


def main() -> None:
    configure_logging()
    settings = load_settings()
    validate_runtime_settings(settings)
    application = build_application(settings)
    application.run_polling(close_loop=False)
