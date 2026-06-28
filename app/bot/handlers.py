from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Awaitable, Callable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.alpha.engine import AlphaEngine
from app.alpha.models import AlphaMarketMovement, AlphaWatchlistCandidate
from app.analysis.classification import MarketType, classify_market
from app.analysis.forecast import (
    ForecastError,
    OpenAIForecastClient,
    deterministic_forecast_fallback,
)
from app.analysis.filtering import UNKNOWN_TOP_CATEGORY_MESSAGE, filter_top_markets, parse_top_category
from app.analysis.movement import calculate_snapshot_movement
from app.analysis.risk import calculate_risk_score
from app.analysis.scoring import calculate_signal_score, determine_verdict
from app.bot.messages import (
    event_has_no_active_markets_message,
    format_alpha_report,
    format_calibration_summary,
    format_catalyst_message,
    format_daily_digest,
    format_edge_opportunities,
    format_forecast_message,
    format_signal_history,
    format_status_message,
    format_unwatch_result,
    format_watch_confirmation,
    format_watchlist,
    friendly_api_error,
    market_not_found_message,
    private_bot_message,
    start_message,
    usage_message,
)
from app.bot.status import (
    EDGE_MARKETS_SCANNED_KEY,
    EDGE_QUALIFIED_COUNT_KEY,
    LAST_ALPHA_RUN_KEY,
    LAST_EDGE_SCAN_KEY,
    POLYMARKET_API_STATUS_KEY,
    build_status_data,
)
from app.bot.top import build_top_response, top_fetch_limit
from app.config import Settings
from app.db.repository import MarketRepository
from app.news.catalyst import analyze_market_catalysts
from app.news.client import NewsClient
from app.news.models import NewsScanResult
from app.news.schemas import CatalystAnalysis
from app.opportunity.models import OpportunityScanResult
from app.opportunity.history import OpportunityHistoryRepository
from app.opportunity.scanner import OpportunityScanner, normalize_opportunity_category
from app.polymarket.client import (
    EventHasNoActiveMarketsError,
    MarketLookupError,
    MarketNotFoundError,
    PolymarketAPIError,
    PolymarketClient,
)
from app.polymarket.parser import MarketParseError, parse_market_input, parse_market_slug


logger = logging.getLogger(__name__)
Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]
EDGE_CACHE_KEY = "edge_scan_cache"
EDGE_NEWS_CACHE_KEY = "edge_news_cache"
EDGE_CACHE_TTL = timedelta(minutes=15)
EDGE_FETCH_LIMIT = 250
EDGE_NEWS_ENRICH_LIMIT = 5


def _is_allowed(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return bool(user and user.id == settings.telegram_allowed_user_id)


def private_only(handler: Handler) -> Handler:
    @wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        settings: Settings = context.application.bot_data["settings"]
        if not _is_allowed(update, settings):
            if update.effective_message:
                await update.effective_message.reply_text(private_bot_message())
            return
        await handler(update, context)

    return wrapper


def _arg_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    return " ".join(context.args or []).strip()


def _set_polymarket_api_status(context: ContextTypes.DEFAULT_TYPE, status: str) -> None:
    context.application.bot_data[POLYMARKET_API_STATUS_KEY] = status


def _dedupe_markets(markets: list) -> list:
    seen: set[str] = set()
    unique = []
    for market in markets:
        key = market.slug or market.market_id or market.title
        if key in seen:
            continue
        seen.add(key)
        unique.append(market)
    return unique


def _dedupe_market_rows(
    rows: list[tuple],
) -> list[tuple]:
    seen: set[str] = set()
    unique = []
    for row in rows:
        market = row[0]
        key = market.slug or market.market_id
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def _row_verdict(row: tuple) -> str:
    _market, signal, risk, classification = row
    return determine_verdict(signal.total, risk.total, classification.labels)


def _get_news_client(context: ContextTypes.DEFAULT_TYPE) -> NewsClient:
    news_client = context.application.bot_data.get("news_client")
    if news_client is not None:
        return news_client
    return NewsClient()


def _catalyst_digest_note(catalyst: CatalystAnalysis | NewsScanResult) -> str:
    if isinstance(catalyst, NewsScanResult):
        if catalyst.provider == "none":
            return "scanner not configured"
        if not catalyst.news_found:
            return "none detected"
        return f"score {catalyst.catalyst_score}/100, {catalyst.sentiment}"
    if catalyst.scanner_not_configured:
        return "scanner not configured"
    if catalyst.possible_catalyst and catalyst.possible_catalyst != "none detected":
        return f"possible catalyst: {catalyst.possible_catalyst}"
    return "none detected"


async def _scan_market_news_for_row(
    row: tuple,
    news_client: NewsClient,
) -> NewsScanResult | CatalystAnalysis:
    market, _signal, _risk, classification = row
    if hasattr(news_client, "scan_market"):
        return await news_client.scan_market(market.title, classification.display)
    return await analyze_market_catalysts(market, news_client, classification)


async def _build_daily_catalyst_notes(
    rows: list[tuple],
    news_client: NewsClient,
) -> tuple[dict[str, str], list[tuple[object, NewsScanResult]]]:
    unique_rows = _dedupe_market_rows(rows)
    notes: dict[str, str] = {}
    strong: list[tuple[object, NewsScanResult]] = []
    if not unique_rows:
        return notes, strong
    if not news_client.configured:
        for row in unique_rows:
            market = row[0]
            notes[market.slug or market.market_id] = "scanner not configured"
        return notes, strong
    for row in unique_rows:
        market = row[0]
        catalyst = await _scan_market_news_for_row(row, news_client)
        notes[market.slug or market.market_id] = _catalyst_digest_note(catalyst)
        if isinstance(catalyst, NewsScanResult) and catalyst.catalyst_score >= 51:
            strong.append((market, catalyst))
    strong.sort(key=lambda item: item[1].catalyst_score, reverse=True)
    return notes, strong[:3]


def _previous_snapshots_by_slug(repository: MarketRepository) -> dict[str, object]:
    previous = {}
    for watch_record in repository.list_watches():
        snapshot = repository.get_latest_snapshot(watch_record.id)
        if snapshot:
            previous[watch_record.slug] = snapshot
    return previous


def _cached_edge_result(
    context: ContextTypes.DEFAULT_TYPE,
    category: str,
) -> OpportunityScanResult | None:
    cache = context.application.bot_data.get(EDGE_CACHE_KEY)
    if not isinstance(cache, dict):
        return None
    cached = cache.get(category)
    if not isinstance(cached, OpportunityScanResult) or cached.generated_at is None:
        return None
    generated_at = cached.generated_at if cached.generated_at.tzinfo else cached.generated_at.replace(tzinfo=UTC)
    if datetime.now(UTC) - generated_at > EDGE_CACHE_TTL:
        return None
    return cached


def _store_edge_result(
    context: ContextTypes.DEFAULT_TYPE,
    result: OpportunityScanResult,
) -> None:
    cache = context.application.bot_data.setdefault(EDGE_CACHE_KEY, {})
    cache[result.category] = result
    context.application.bot_data[LAST_EDGE_SCAN_KEY] = result.generated_at
    context.application.bot_data[EDGE_MARKETS_SCANNED_KEY] = result.markets_scanned
    context.application.bot_data[EDGE_QUALIFIED_COUNT_KEY] = result.qualified_count


def _history_repository(repository: object) -> OpportunityHistoryRepository | None:
    database_path = getattr(repository, "database_path", None)
    if not isinstance(database_path, str):
        return None
    return OpportunityHistoryRepository(database_path)


def _cached_edge_news_notes(
    context: ContextTypes.DEFAULT_TYPE,
    result: OpportunityScanResult,
) -> dict[str, str] | None:
    if result.generated_at is None:
        return None
    cache = context.application.bot_data.get(EDGE_NEWS_CACHE_KEY)
    if not isinstance(cache, dict):
        return None
    cached = cache.get(result.category)
    if not isinstance(cached, tuple) or len(cached) != 2:
        return None
    cached_at, notes = cached
    if cached_at != result.generated_at or not isinstance(notes, dict):
        return None
    return notes


def _store_edge_news_notes(
    context: ContextTypes.DEFAULT_TYPE,
    result: OpportunityScanResult,
    notes: dict[str, str],
) -> None:
    if result.generated_at is None:
        return
    cache = context.application.bot_data.setdefault(EDGE_NEWS_CACHE_KEY, {})
    cache[result.category] = (result.generated_at, notes)


async def _build_edge_news_notes(
    context: ContextTypes.DEFAULT_TYPE,
    result: OpportunityScanResult,
) -> dict[str, str]:
    cached = _cached_edge_news_notes(context, result)
    if cached is not None:
        return cached
    news_client = _get_news_client(context)
    selected = result.candidates[:EDGE_NEWS_ENRICH_LIMIT]
    logger.info(
        "EDGE_NEWS_ENRICHMENT_START requested_category=%s selected_count=%s",
        result.category,
        len(selected),
    )
    notes: dict[str, str] = {}
    if not selected or not getattr(news_client, "configured", False):
        _store_edge_news_notes(context, result, notes)
        return notes
    for candidate in selected:
        try:
            scan = await news_client.scan_market(
                candidate.question,
                candidate.category,
                use_openai=False,
            )
        except TypeError:
            scan = await news_client.scan_market(candidate.question, candidate.category)
        except Exception as exc:
            logger.warning(
                "EDGE_NEWS_ENRICHMENT_FAILED market_id=%s error_class=%s message=%s",
                candidate.market_id,
                exc.__class__.__name__,
                str(exc)[:240],
            )
            continue
        key = candidate.market_slug or candidate.market_id
        if scan.news_found:
            notes[key] = (
                f"score {scan.catalyst_score}/100, {scan.sentiment}; "
                f"{scan.items[0].title if scan.items else scan.summary}"
            )
        else:
            notes[key] = "no relevant external catalyst found"
    _store_edge_news_notes(context, result, notes)
    return notes


async def _build_alpha_watchlist_context(
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[list[AlphaWatchlistCandidate], list[AlphaMarketMovement]]:
    repository: MarketRepository = context.application.bot_data["repository"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    rows: list[AlphaWatchlistCandidate] = []
    movements: list[AlphaMarketMovement] = []
    for watch_record in repository.list_watches():
        history = repository.list_recent_snapshots(watch_record.id)
        previous = history[0] if history else None
        try:
            market = await client.fetch_market_by_slug(watch_record.slug)
        except PolymarketAPIError:
            _set_polymarket_api_status(context, "unavailable")
            continue
        except MarketLookupError:
            _set_polymarket_api_status(context, "ok")
            continue
        _set_polymarket_api_status(context, "ok")
        movement = calculate_snapshot_movement(market, history)
        repository.add_snapshot(watch_record.id, market)
        signal = calculate_signal_score(market, previous)
        risk = calculate_risk_score(market)
        classification = classify_market(market, signal_score=signal.total)
        rows.append(AlphaWatchlistCandidate(market, signal, risk, classification))
        movements.append(AlphaMarketMovement(market, movement))
    return rows, movements


async def _build_alpha_catalysts(
    context: ContextTypes.DEFAULT_TYPE,
    result: OpportunityScanResult,
    watchlist_rows: list[AlphaWatchlistCandidate],
) -> dict[str, NewsScanResult]:
    news_client = _get_news_client(context)
    if not getattr(news_client, "configured", False):
        return {}
    selected: list[tuple[str, str, str]] = []
    for candidate in result.candidates[:EDGE_NEWS_ENRICH_LIMIT]:
        selected.append((candidate.market_slug or candidate.market_id, candidate.question, candidate.category))
    watched = sorted(
        watchlist_rows,
        key=lambda row: (row.signal.total, -row.risk.total),
        reverse=True,
    )[:3]
    for row in watched:
        selected.append((row.market.slug or row.market.market_id, row.market.title, row.classification.display))

    scans: dict[str, NewsScanResult] = {}
    seen: set[str] = set()
    for key, question, market_type in selected:
        if not key or key in seen:
            continue
        seen.add(key)
        try:
            try:
                scan = await news_client.scan_market(question, market_type, use_openai=False)
            except TypeError:
                scan = await news_client.scan_market(question, market_type)
        except Exception as exc:
            logger.warning(
                "ALPHA_CATALYST_SCAN_FAILED market_key=%s error_class=%s message=%s",
                key,
                exc.__class__.__name__,
                str(exc)[:240],
            )
            continue
        scans[key] = scan
    return scans


def _attach_alpha_catalysts(
    rows: list[AlphaWatchlistCandidate],
    catalysts: dict[str, NewsScanResult],
) -> list[AlphaWatchlistCandidate]:
    enriched = []
    for row in rows:
        scan = (
            catalysts.get(row.market.slug)
            or catalysts.get(row.market.market_id)
            or catalysts.get(row.market.title)
        )
        enriched.append(
            AlphaWatchlistCandidate(
                row.market,
                row.signal,
                row.risk,
                row.classification,
                scan,
            )
        )
    return enriched


async def _run_edge_scan(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    category: str = "all",
    force_refresh: bool = False,
    fetch_limit: int = EDGE_FETCH_LIMIT,
) -> OpportunityScanResult:
    normalized = normalize_opportunity_category(category)
    logger.info(
        "EDGE_SCAN_START requested_category=%s fetch_limit=%s force_refresh=%s",
        normalized,
        fetch_limit,
        force_refresh,
    )
    cached = None if force_refresh else _cached_edge_result(context, normalized)
    if cached is not None:
        logger.info(
            "EDGE_MARKET_SCAN_COMPLETED requested_category=%s cache_hit=true markets_scanned=%s qualified_opportunities=%s",
            normalized,
            cached.markets_scanned,
            cached.qualified_count,
        )
        return cached

    repository: MarketRepository = context.application.bot_data["repository"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    history = _history_repository(repository)
    calibration = history.metrics() if history else None
    active_markets = await client.fetch_active_markets(limit=fetch_limit)
    watched_markets = []
    for watch_record in repository.list_watches():
        try:
            watched_markets.append(await client.fetch_market_by_slug(watch_record.slug))
        except MarketLookupError:
            continue
    markets = _dedupe_markets(active_markets + watched_markets)
    logger.info(
        "EDGE_MARKETS_FETCHED requested_category=%s active_count=%s watched_count=%s deduped_count=%s",
        normalized,
        len(active_markets),
        len(watched_markets),
        len(markets),
    )
    scanner = OpportunityScanner()
    result = scanner.scan(
        markets,
        category=normalized,
        previous_by_slug=_previous_snapshots_by_slug(repository),
        calibration_metrics=calibration.by_category if calibration else None,
        limit=10,
    )
    _store_edge_result(context, result)
    logger.info(
        "EDGE_TOP_SELECTED requested_category=%s markets_before_filter=%s markets_after_filter=%s "
        "qualified_opportunities=%s top_selected=%s",
        normalized,
        result.markets_scanned,
        result.filtered_count,
        result.qualified_count,
        len(result.candidates),
    )
    logger.info(
        "EDGE_MARKET_SCAN_COMPLETED requested_category=%s cache_hit=false markets_scanned=%s qualified_opportunities=%s",
        normalized,
        result.markets_scanned,
        result.qualified_count,
    )
    return result


@private_only
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(start_message())


@private_only
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    repository: MarketRepository = context.application.bot_data["repository"]
    status_data = build_status_data(settings, repository, context.application.bot_data)
    await update.effective_message.reply_text(format_status_message(status_data))


@private_only
async def top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    raw_text = update.effective_message.text if update.effective_message else ""
    parsed_args = list(context.args or [])
    try:
        requested_filter = parse_top_category(parsed_args)
    except ValueError:
        logger.info(
            "top_filter_parse_failed raw_text=%r parsed_args=%r",
            raw_text,
            parsed_args,
        )
        await update.effective_message.reply_text(UNKNOWN_TOP_CATEGORY_MESSAGE)
        return
    logger.info(
        "top_filter_parse raw_text=%r parsed_args=%r parsed_category=%s",
        raw_text,
        parsed_args,
        requested_filter,
    )
    fetch_limit = top_fetch_limit(requested_filter)
    try:
        markets = await client.fetch_active_markets(limit=fetch_limit)
    except PolymarketAPIError:
        _set_polymarket_api_status(context, "unavailable")
        await update.effective_message.reply_text(friendly_api_error())
        return
    _set_polymarket_api_status(context, "ok")
    top_response = build_top_response(
        requested_filter,
        markets,
        meme_allow_volume_threshold=settings.meme_allow_volume_threshold,
    )
    logger.info(
        (
            "top_filter_result category=%s fetched_count=%s filtered_count=%s "
            "first_fetched_titles=%r first_filtered_titles=%r fallback_used=%s "
            "final_rendered_count=%s first_rendered_titles=%r"
        ),
        requested_filter,
        top_response.fetched_count,
        top_response.filtered_count,
        top_response.first_fetched_titles,
        top_response.first_filtered_titles,
        top_response.fallback_used,
        len(top_response.rows),
        [row[0].title for row in top_response.rows[:5]],
    )
    await update.effective_message.reply_text(top_response.message)


@private_only
async def edge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_category = _arg_text(context) or "all"
    try:
        category = normalize_opportunity_category(raw_category)
    except ValueError:
        await update.effective_message.reply_text(usage_message("edge"))
        return
    try:
        result = await _run_edge_scan(context, category=category)
    except PolymarketAPIError:
        _set_polymarket_api_status(context, "unavailable")
        await update.effective_message.reply_text(friendly_api_error())
        return
    _set_polymarket_api_status(context, "ok")
    repository: MarketRepository = context.application.bot_data["repository"]
    history = _history_repository(repository)
    if history:
        history.record_predictions(result.candidates)
    catalyst_notes = await _build_edge_news_notes(context, result)
    logger.info(
        "EDGE_COMPLETED requested_category=%s markets_scanned=%s qualified_opportunities=%s "
        "news_enriched_count=%s",
        result.category,
        result.markets_scanned,
        result.qualified_count,
        len(catalyst_notes),
    )
    await update.effective_message.reply_text(
        format_edge_opportunities(result.candidates, result.category, catalyst_notes)
    )


@private_only
async def alpha(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_category = _arg_text(context) or "all"
    try:
        category = normalize_opportunity_category(raw_category)
    except ValueError:
        await update.effective_message.reply_text(usage_message("alpha"))
        return
    try:
        edge_result = await _run_edge_scan(context, category=category)
    except PolymarketAPIError:
        _set_polymarket_api_status(context, "unavailable")
        await update.effective_message.reply_text(friendly_api_error())
        return
    repository: MarketRepository = context.application.bot_data["repository"]
    watchlist_rows, movements = await _build_alpha_watchlist_context(context)
    catalysts = await _build_alpha_catalysts(context, edge_result, watchlist_rows)
    watchlist_rows = _attach_alpha_catalysts(watchlist_rows, catalysts)
    calibration_summary = OpportunityHistoryRepository(repository.database_path).metrics()
    report = AlphaEngine().build_report(
        opportunities=edge_result.candidates,
        catalysts=catalysts,
        watchlist=watchlist_rows,
        movements=movements,
        calibration_summary=calibration_summary,
        category=category,
    )
    context.application.bot_data[LAST_ALPHA_RUN_KEY] = report.generated_at
    await update.effective_message.reply_text(format_alpha_report(report))


@private_only
async def calibration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: MarketRepository = context.application.bot_data["repository"]
    summary = OpportunityHistoryRepository(repository.database_path).metrics()
    await update.effective_message.reply_text(format_calibration_summary(summary))


@private_only
async def watch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: MarketRepository = context.application.bot_data["repository"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    try:
        slug = parse_market_slug(_arg_text(context))
    except MarketParseError:
        await update.effective_message.reply_text(usage_message("watch"))
        return
    try:
        market = await client.fetch_market_by_slug(slug)
    except EventHasNoActiveMarketsError:
        _set_polymarket_api_status(context, "ok")
        await update.effective_message.reply_text(event_has_no_active_markets_message())
        return
    except MarketNotFoundError:
        _set_polymarket_api_status(context, "ok")
        await update.effective_message.reply_text(market_not_found_message())
        return
    except PolymarketAPIError:
        _set_polymarket_api_status(context, "unavailable")
        await update.effective_message.reply_text(friendly_api_error())
        return
    _set_polymarket_api_status(context, "ok")
    watch_record = repository.add_watch(market)
    repository.add_snapshot(watch_record.id, market)
    await update.effective_message.reply_text(format_watch_confirmation(watch_record, market))


@private_only
async def watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: MarketRepository = context.application.bot_data["repository"]
    watches = repository.list_watches()
    latest = {watch.id: repository.get_latest_snapshot(watch.id) for watch in watches}
    await update.effective_message.reply_text(format_watchlist(watches, latest))


@private_only
async def unwatch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = _arg_text(context)
    try:
        watch_id = int(raw)
    except ValueError:
        await update.effective_message.reply_text(usage_message("unwatch"))
        return
    repository: MarketRepository = context.application.bot_data["repository"]
    removed = repository.remove_watch(watch_id)
    await update.effective_message.reply_text(format_unwatch_result(removed, watch_id))


@private_only
async def forecast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: MarketRepository = context.application.bot_data["repository"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    ai_client: OpenAIForecastClient = context.application.bot_data["ai_forecast_client"]
    news_client = _get_news_client(context)
    raw_arg = _arg_text(context)
    logger.info("forecast_lookup_input input_argument=%r", raw_arg)
    try:
        parsed_input = parse_market_input(raw_arg)
    except MarketParseError:
        if raw_arg:
            await update.effective_message.reply_text(market_not_found_message())
        else:
            await update.effective_message.reply_text(usage_message("forecast"))
        return
    logger.info(
        "forecast_lookup_parsed input_argument=%r parsed_slug=%s input_type=%s",
        raw_arg,
        parsed_input.slug,
        parsed_input.input_type,
    )
    try:
        if parsed_input.input_type == "text_query":
            market = await client.fetch_market_by_text_query(
                parsed_input.slug,
                log_context="forecast",
            )
        else:
            market = await client.fetch_market_for_input(
                parsed_input.slug,
                parsed_input.input_type,
                log_context="forecast",
            )
    except EventHasNoActiveMarketsError:
        _set_polymarket_api_status(context, "ok")
        await update.effective_message.reply_text(event_has_no_active_markets_message())
        return
    except MarketNotFoundError:
        _set_polymarket_api_status(context, "ok")
        await update.effective_message.reply_text(market_not_found_message())
        return
    except PolymarketAPIError:
        _set_polymarket_api_status(context, "unavailable")
        await update.effective_message.reply_text(friendly_api_error())
        return
    _set_polymarket_api_status(context, "ok")

    logger.info(
        "forecast_lookup_result input_argument=%r parsed_slug=%s selected_market_id=%s selected_question=%r",
        raw_arg,
        parsed_input.slug,
        market.market_id,
        market.title,
    )
    watch_record = repository.find_watch_by_slug(market.slug)
    previous = repository.get_latest_snapshot(watch_record.id) if watch_record else None
    history = repository.list_recent_snapshots(watch_record.id) if watch_record else []
    movement = calculate_snapshot_movement(market, history)
    signal = calculate_signal_score(market, previous)
    risk = calculate_risk_score(market)
    classification = classify_market(market, signal_score=signal.total)
    catalyst = await news_client.scan_market(market.title, classification.display)
    # V2.4: Fetch external context (CoinGecko + FRED + FiveThirtyEight) in parallel
    # to enrich the AI forecast prompt with real-world data.
    settings: Settings = context.application.bot_data["settings"]
    external_context = ""
    try:
        from app.external.aggregator import build_external_context
        external_context = await build_external_context(
            market.title,
            fred_api_key=settings.fred_api_key,
            timeout_seconds=settings.request_timeout_seconds,
        )
        if external_context:
            logger.info(
                "forecast_external_context_fetched market_id=%s context_length=%s",
                market.market_id,
                len(external_context),
            )
    except Exception as exc:
        logger.warning(
            "forecast_external_context_failed market_id=%s error_class=%s msg=%s",
            market.market_id,
            exc.__class__.__name__,
            str(exc)[:240],
        )
    try:
        ai_forecast = await ai_client.generate(
            market,
            signal,
            risk,
            previous,
            catalyst,
            external_context=external_context or None,
        )
    except ForecastError as exc:
        logger.info("AI forecast unavailable: %s", exc)
        ai_forecast = deterministic_forecast_fallback(signal, risk, str(exc))

    if watch_record:
        repository.add_snapshot(watch_record.id, market)
        repository.add_signal(
            watch_record.id,
            signal.total,
            risk.total,
            determine_verdict(signal.total, risk.total, classification.labels),
            signal.reason,
        )

    await update.effective_message.reply_text(
        format_forecast_message(
            market,
            signal,
            risk,
            ai_forecast,
            classification,
            movement,
            catalyst,
            edge_threshold=context.application.bot_data["settings"].min_signal_score,
            external_context=external_context or None,
        )
    )


@private_only
async def catalyst(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    news_client = _get_news_client(context)
    raw_arg = _arg_text(context)
    try:
        parsed_input = parse_market_input(raw_arg)
    except MarketParseError:
        if raw_arg:
            await update.effective_message.reply_text(market_not_found_message())
        else:
            await update.effective_message.reply_text(usage_message("catalyst"))
        return
    try:
        if parsed_input.input_type == "text_query":
            market = await client.fetch_market_by_text_query(
                parsed_input.slug,
                log_context="forecast",
            )
        else:
            market = await client.fetch_market_for_input(
                parsed_input.slug,
                parsed_input.input_type,
                log_context="forecast",
            )
    except EventHasNoActiveMarketsError:
        _set_polymarket_api_status(context, "ok")
        await update.effective_message.reply_text(event_has_no_active_markets_message())
        return
    except MarketNotFoundError:
        _set_polymarket_api_status(context, "ok")
        await update.effective_message.reply_text(market_not_found_message())
        return
    except PolymarketAPIError:
        _set_polymarket_api_status(context, "unavailable")
        await update.effective_message.reply_text(friendly_api_error())
        return
    _set_polymarket_api_status(context, "ok")
    classification = classify_market(market)
    catalyst_analysis = await news_client.scan_market(market.title, classification.display)
    await update.effective_message.reply_text(format_catalyst_message(market, catalyst_analysis))


@private_only
async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings: Settings = context.application.bot_data["settings"]
    repository: MarketRepository = context.application.bot_data["repository"]
    client: PolymarketClient = context.application.bot_data["polymarket_client"]
    news_client = _get_news_client(context)
    watched_rows = []
    for watch_record in repository.list_watches():
        try:
            previous = repository.get_latest_snapshot(watch_record.id)
            market = await client.fetch_market_by_slug(watch_record.slug)
            repository.add_snapshot(watch_record.id, market)
        except PolymarketAPIError:
            _set_polymarket_api_status(context, "unavailable")
            continue
        except MarketLookupError:
            _set_polymarket_api_status(context, "ok")
            continue
        _set_polymarket_api_status(context, "ok")
        signal = calculate_signal_score(market, previous)
        risk = calculate_risk_score(market)
        classification = classify_market(market, signal_score=signal.total)
        verdict = determine_verdict(signal.total, risk.total, classification.labels)
        repository.add_signal(watch_record.id, signal.total, risk.total, verdict, signal.reason)
        watched_rows.append((market, signal, risk, classification))

    active_rows = []
    try:
        active_markets = await client.fetch_active_markets(limit=40)
    except PolymarketAPIError:
        _set_polymarket_api_status(context, "unavailable")
        active_markets = []
    else:
        _set_polymarket_api_status(context, "ok")
    active_rows = filter_top_markets(
        active_markets,
        "default",
        limit=10,
        meme_allow_volume_threshold=settings.meme_allow_volume_threshold,
    )

    strong_watched = sorted(
        [
            row
            for row in watched_rows
            if row[1].total >= settings.min_signal_score
            and _row_verdict(row) in {"STRONG SIGNAL", "WATCH"}
        ],
        key=lambda item: (item[1].total, -item[2].total),
        reverse=True,
    )[:3]
    interesting_watched = sorted(
        [
            row
            for row in watched_rows
            if row[1].total < settings.min_signal_score
            and _row_verdict(row) in {"WATCH", "INTERESTING BUT RISKY"}
        ],
        key=lambda item: (item[1].total, item[0].volume or 0),
        reverse=True,
    )[:3]
    high_volume_active = sorted(
        active_rows,
        key=lambda item: (item[0].volume or 0, item[1].total),
        reverse=True,
    )[:3]
    combined_rows = _dedupe_market_rows(watched_rows + active_rows)
    avoid_risky = sorted(
        [
            row
            for row in combined_rows
            if _row_verdict(row) in {"AVOID", "LOTTERY STYLE", "HIGH VOLUME / NO EDGE"}
            or row[2].total >= 65
            or any(
                label
                in {
                    MarketType.LOW_LIQUIDITY,
                    MarketType.AMBIGUOUS_RESOLUTION,
                }
                for label in row[3].labels
            )
        ],
        key=lambda item: item[2].total,
        reverse=True,
    )[:3]
    ignore_markets = [
        row
        for row in combined_rows
        if row[1].total < settings.min_signal_score
        and any(
            label in {MarketType.MEME, MarketType.LOTTERY, MarketType.EXTREME_PROBABILITY}
            for label in row[3].labels
        )
    ][:3]
    catalyst_notes, strong_catalyst_markets = await _build_daily_catalyst_notes(
        strong_watched + interesting_watched + high_volume_active + avoid_risky + ignore_markets,
        news_client,
    )
    try:
        edge_result = await _run_edge_scan(context, category="all")
    except PolymarketAPIError:
        edge_result = OpportunityScanResult()
    calibration_summary = OpportunityHistoryRepository(repository.database_path).metrics()
    daily_catalysts = {
        market.slug or market.market_id: scan
        for market, scan in strong_catalyst_markets
    }
    alpha_watchlist = [
        AlphaWatchlistCandidate(
            market,
            signal,
            risk,
            classification,
            daily_catalysts.get(market.slug or market.market_id),
        )
        for market, signal, risk, classification in watched_rows
    ]
    alpha_report = AlphaEngine().build_report(
        opportunities=edge_result.candidates,
        catalysts=daily_catalysts,
        watchlist=alpha_watchlist,
        calibration_summary=calibration_summary,
        category="all",
    )
    context.application.bot_data[LAST_ALPHA_RUN_KEY] = alpha_report.generated_at
    await update.effective_message.reply_text(
        format_daily_digest(
            strong_watched,
            interesting_watched,
            high_volume_active,
            avoid_risky,
            ignore_markets,
            catalyst_notes,
            strong_catalyst_markets,
            edge_result.candidates[:3],
            calibration_summary,
            alpha_report,
        )
    )


@private_only
async def signals(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    repository: MarketRepository = context.application.bot_data["repository"]
    records = repository.list_recent_signals(limit=10)
    await update.effective_message.reply_text(format_signal_history(records))


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("top", top))
    application.add_handler(CommandHandler("edge", edge))
    application.add_handler(CommandHandler("alpha", alpha))
    application.add_handler(CommandHandler("watch", watch))
    application.add_handler(CommandHandler("watchlist", watchlist))
    application.add_handler(CommandHandler("unwatch", unwatch))
    application.add_handler(CommandHandler("forecast", forecast))
    application.add_handler(CommandHandler("catalyst", catalyst))
    application.add_handler(CommandHandler("calibration", calibration))
    application.add_handler(CommandHandler("daily", daily))
    application.add_handler(CommandHandler("signals", signals))
