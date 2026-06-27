from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram.ext import ContextTypes

from app.analysis.classification import classify_market
from app.analysis.risk import calculate_risk_score
from app.analysis.scoring import calculate_signal_score, determine_verdict
from app.bot.messages import format_alert_message
from app.bot.status import LAST_SCHEDULED_CHECK_KEY, POLYMARKET_API_STATUS_KEY
from app.config import Settings
from app.db.repository import MarketRepository
from app.opportunity.history import OpportunityHistoryRecord, OpportunityHistoryRepository, OpportunityResolutionUpdater
from app.polymarket.client import MarketLookupError, PolymarketAPIError, PolymarketClient
from app.polymarket.schemas import MarketData


logger = logging.getLogger(__name__)


async def check_watchlist_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    repository: MarketRepository = context.application.bot_data["repository"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    context.application.bot_data[LAST_SCHEDULED_CHECK_KEY] = datetime.now(UTC)

    for watch in repository.list_watches():
        try:
            previous = repository.get_latest_snapshot(watch.id)
            market = await client.fetch_market_by_slug(watch.slug)
            context.application.bot_data[POLYMARKET_API_STATUS_KEY] = "ok"
            repository.add_snapshot(watch.id, market)
            signal = calculate_signal_score(market, previous)
            risk = calculate_risk_score(market)
            classification = classify_market(market, signal_score=signal.total)
            verdict = determine_verdict(signal.total, risk.total, classification.labels)
            repository.add_signal(
                watch.id,
                signal.total,
                risk.total,
                verdict,
                signal.reason,
            )
            if signal.total >= settings.min_signal_score:
                await context.bot.send_message(
                    chat_id=settings.telegram_allowed_user_id,
                    text=format_alert_message(market, signal, risk),
                )
        except PolymarketAPIError:
            context.application.bot_data[POLYMARKET_API_STATUS_KEY] = "unavailable"
            logger.warning("Polymarket API failed for watch %s", watch.slug, exc_info=True)
        except MarketLookupError:
            context.application.bot_data[POLYMARKET_API_STATUS_KEY] = "ok"
            logger.info("No active Polymarket market found for watch %s", watch.slug)
        except Exception:
            logger.exception("Scheduled watchlist check failed for %s", watch.slug)


async def opportunity_resolution_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: MarketRepository = context.application.bot_data["repository"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    history = OpportunityHistoryRepository(repository.database_path)

    async def resolver(record: OpportunityHistoryRecord) -> MarketData | None:
        try:
            return await client.fetch_market_by_slug(record.market_id, enrich=False)
        except MarketLookupError:
            return None
        except PolymarketAPIError:
            context.application.bot_data[POLYMARKET_API_STATUS_KEY] = "unavailable"
            return None

    updater = OpportunityResolutionUpdater(history, resolver)
    updated = await updater.update_resolutions(limit=200)
    logger.info("opportunity_resolution_update updated_count=%s", updated)
