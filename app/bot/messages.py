from __future__ import annotations

from datetime import UTC, datetime

from app.alpha.models import AlphaReport
from app.analysis.classification import (
    MarketClassification,
    MarketType,
    classify_market,
    market_type_notes,
)
from app.analysis.movement import SnapshotMovement, format_snapshot_movement
from app.analysis.scoring import determine_verdict
from app.bot.status import BotStatusData
from app.db.models import SignalHistoryRecord, WatchedMarket
from app.news.models import NewsItem, NewsScanResult
from app.news.schemas import CatalystAnalysis
from app.opportunity.history import CalibrationSummary
from app.opportunity.models import OpportunityCandidate
from app.polymarket.schemas import AIForecast, MarketData, ScoreBreakdown


MarketAnalysisRow = tuple[MarketData, ScoreBreakdown, ScoreBreakdown, MarketClassification]
CatalystLike = CatalystAnalysis | NewsScanResult


def fmt_price(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def fmt_number(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def fmt_date(value: datetime | None) -> str:
    if value is None:
        return "n/a"
    return value.strftime("%Y-%m-%d %H:%M UTC")


def fmt_yes_no(value: bool) -> str:
    return "yes" if value else "no"


def fmt_enabled(value: bool) -> str:
    return "enabled" if value else "disabled"


def fmt_percent(value: float) -> str:
    return f"{value:.0%}"


def fmt_age(value: datetime) -> str:
    published = value if value.tzinfo else value.replace(tzinfo=UTC)
    hours = max(0, int((datetime.now(UTC) - published).total_seconds() // 3600))
    if hours < 1:
        return "less than 1h ago"
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def risk_label(risk_score: int) -> str:
    if risk_score >= 75:
        return "HIGH RISK"
    if risk_score >= 45:
        return "MEDIUM RISK"
    return "LOWER RISK"


def verdict_label(verdict: str) -> str:
    if verdict in {
        "STRONG SIGNAL",
        "WATCH",
        "INTERESTING BUT RISKY",
        "HIGH VOLUME / NO EDGE",
        "LOTTERY STYLE",
        "AVOID",
        "LOW PRIORITY",
    }:
        return verdict
    if "AVOID" in verdict:
        return "AVOID"
    return "LOW PRIORITY"


def start_message() -> str:
    return (
        "Personal Polymarket Forecast Bot V2\n\n"
        "This private bot analyzes Polymarket markets and sends forecasts, signals, "
        "risk warnings, and daily digests.\n\n"
        "Analysis-only: it does not place trades, does not bypass geoblocking, "
        "does not automate orders, and does not make guaranteed profit claims."
    )


def private_bot_message() -> str:
    return "This is a private bot."


def usage_message(command: str) -> str:
    examples = {
        "watch": "/watch https://polymarket.com/event/fed-decision-in-october\n/watch fed-decision-in-october",
        "forecast": "/forecast https://polymarket.com/event/fed-decision-in-october\n/forecast fed-decision-in-october",
        "catalyst": "/catalyst https://polymarket.com/event/fed-decision-in-october\n/catalyst fed-decision-in-october",
        "edge": "/edge\n/edge crypto\n/edge politics\n/edge sports\n/edge macro",
        "alpha": "/alpha\n/alpha crypto\n/alpha politics\n/alpha sports\n/alpha macro",
        "top": "/top\n/top crypto\n/top politics\n/top macro\n/top sports\n/top all",
        "unwatch": "/unwatch 3",
    }
    return f"Usage:\n{examples.get(command, command)}"


def friendly_api_error() -> str:
    return "Polymarket API is unavailable right now."


def market_not_found_message() -> str:
    return "I could not find an active Polymarket market for this slug or URL."


def event_has_no_active_markets_message() -> str:
    return "I found the event, but there are no active markets inside it."


def format_market_line(index: int, row: MarketAnalysisRow) -> str:
    market, signal, risk, classification = row
    verdict = determine_verdict(signal.total, risk.total, classification.labels)
    why = _quick_reason(classification)
    return (
        f"{index}. {market.title}\n"
        f"   YES: {fmt_price(market.yes_price)} | Vol: {fmt_number(market.volume)} | "
        f"Liq: {fmt_number(market.liquidity)}\n"
        f"   Type: {classification.display}\n"
        f"   Verdict: {verdict_label(verdict)} | Risk: {risk_label(risk.total)}\n"
        f"   Why: {why}"
    )


def format_top_markets(rows: list[MarketAnalysisRow], filter_name: str = "default") -> str:
    if not rows:
        if filter_name in {"crypto", "politics", "macro", "sports"}:
            return (
                f"No active {filter_name} markets found in the current top results.\n"
                "Try /top all or check again later."
            )
        return "No active markets found for this filter."
    title = "Top active markets" if filter_name == "default" else f"Top active markets: {filter_name}"
    lines = [title, ""]
    for index, row in enumerate(rows, start=1):
        lines.append(format_market_line(index, row))
    return "\n".join(lines)


def format_edge_opportunities(
    opportunities: list[OpportunityCandidate],
    category: str = "all",
    catalyst_notes: dict[str, str] | None = None,
) -> str:
    if not opportunities:
        if category == "all":
            return "No qualified opportunities found right now. Try again later."
        return f"No qualified {category} opportunities found right now. Try /edge or check again later."
    title = "Top opportunities" if category == "all" else f"Top opportunities: {category}"
    lines = [title, ""]
    for index, item in enumerate(opportunities[:10], start=1):
        lines.append(format_edge_opportunity(index, item, catalyst_notes))
        lines.append("")
    lines.append("Analysis-only. Not financial advice. No automated trading.")
    return "\n".join(lines).rstrip()


def format_edge_opportunity(
    index: int,
    item: OpportunityCandidate,
    catalyst_notes: dict[str, str] | None = None,
) -> str:
    text = (
        f"{index}.\n"
        f"{item.question}\n\n"
        f"Market:\n{fmt_price(item.yes_price)}\n\n"
        f"Fair range:\n{_fmt_fair_range(item)}\n\n"
        f"Edge:\n{_fmt_edge_mid(item)}\n\n"
        f"Quality:\n{item.quality_score}\n\n"
        f"Confidence:\n{item.confidence_score}\n\n"
        f"Risk:\n{item.risk_score}\n\n"
        f"Reason:\n{item.reason}"
    )
    note = (catalyst_notes or {}).get(item.market_slug or item.market_id)
    if note:
        text = f"{text}\n\nCatalyst:\n{note}"
    return text


def _fmt_fair_range(item: OpportunityCandidate) -> str:
    if item.fair_probability_min is None or item.fair_probability_max is None:
        return "n/a"
    return f"{item.fair_probability_min:.0%}-{item.fair_probability_max:.0%}"


def _fmt_edge_mid(item: OpportunityCandidate) -> str:
    if (
        item.yes_price is None
        or item.fair_probability_min is None
        or item.fair_probability_max is None
    ):
        return "n/a"
    fair_mid = (item.fair_probability_min + item.fair_probability_max) / 2
    return f"{fair_mid - item.yes_price:+.0%}"


def format_alpha_report(report: AlphaReport) -> str:
    lines = [
        "Morning Alpha Report",
        "",
        "Generated:",
        fmt_date(report.generated_at),
        "",
        f"Alpha score: {report.alpha_score}/100",
        "",
        "Top Opportunity",
    ]
    if report.top_opportunity:
        top = report.top_opportunity
        lines.extend(
            [
                "",
                top.question,
                "",
                "Market:",
                fmt_price(top.yes_price),
                "",
                "Fair:",
                _fmt_fair_range(top),
                "",
                "Edge:",
                _fmt_edge_mid(top),
                "",
                "Confidence:",
                str(top.confidence_score),
                "",
                "Risk:",
                str(top.risk_score),
            ]
        )
    else:
        lines.append("No qualified opportunities found right now.")

    lines.extend(["", "Strongest Catalyst"])
    if report.strongest_catalyst:
        catalyst = report.strongest_catalyst
        lines.extend(
            [
                "",
                catalyst.question,
                "",
                f"Catalyst score: {catalyst.catalyst_score}/100",
                f"Sentiment: {catalyst.sentiment}",
                f"Confidence: {catalyst.confidence}%",
                catalyst.explanation,
            ]
        )
    else:
        lines.append("No strong external catalysts found.")

    lines.extend(["", "Watchlist Alert"])
    if report.watchlist_alert:
        alert = report.watchlist_alert
        lines.extend(
            [
                "",
                alert.market.title,
                f"Signal: {alert.signal_score}/100 | Catalyst: {alert.catalyst_score}/100",
                f"Risk: {alert.risk_score}/100 | Verdict: {verdict_label(alert.verdict)}",
            ]
        )
    else:
        lines.append("No watchlist alerts.")

    lines.extend(["", "Biggest Movers", "", "Top 3 upward movers"])
    if report.upward_movers:
        for index, mover in enumerate(report.upward_movers[:3], start=1):
            lines.append(
                f"{index}. {mover.market.title}\n"
                f"   24h: {mover.change_24h:+.1%} | Current: {fmt_price(mover.current_probability)}"
            )
    else:
        lines.append("No upward movers with 24h history.")

    lines.extend(["", "Top 3 downward movers"])
    if report.downward_movers:
        for index, mover in enumerate(report.downward_movers[:3], start=1):
            lines.append(
                f"{index}. {mover.market.title}\n"
                f"   24h: {mover.change_24h:+.1%} | Current: {fmt_price(mover.current_probability)}"
            )
    else:
        lines.append("No downward movers with 24h history.")

    lines.extend(["", "Highest Risk Market"])
    if report.highest_risk_market:
        risk = report.highest_risk_market
        lines.extend(
            [
                risk.question,
                f"Risk: {risk.risk_score}/100",
                f"Why: {risk.reason}",
            ]
        )
    else:
        lines.append("No non-meme high-risk market found.")

    lines.extend(["", "Model Performance"])
    if report.calibration_summary:
        lines.extend(_format_model_performance(report.calibration_summary))
    else:
        lines.append("No calibration history yet.")

    lines.extend(["", "Analysis only. Not financial advice. No automated trading."])
    return "\n".join(lines)


def format_status_message(status: BotStatusData) -> str:
    return "\n".join(
        [
            "Bot status",
            f"Running: {fmt_yes_no(status.running)}",
            f"Database connected: {fmt_yes_no(status.database_connected)}",
            f"Watchlist count: {status.watchlist_count}",
            f"Last scheduled check: {fmt_date(status.last_scheduled_check_time)}",
            f"OpenAI configured: {fmt_yes_no(status.openai_configured)}",
            f"News provider: {status.news_provider}",
            f"News scanner configured: {fmt_yes_no(status.news_scanner_configured)}",
            f"News lookback hours: {status.news_lookback_hours}",
            f"News max results: {status.news_max_results}",
            f"Polymarket API last status: {status.polymarket_api_last_status}",
            f"CHECK_INTERVAL_MINUTES: {status.check_interval_minutes}",
            f"MIN_SIGNAL_SCORE: {status.min_signal_score}",
            f"Opportunity engine: {fmt_enabled(status.opportunity_engine_enabled)}",
            f"Last edge scan: {fmt_date(status.last_edge_scan_time)}",
            f"Markets scanned: {status.edge_markets_scanned}",
            f"Qualified opportunities: {status.edge_qualified_count}",
            f"Alpha engine: {fmt_enabled(status.alpha_engine_enabled)}",
            f"Last alpha run: {fmt_date(status.last_alpha_run_time)}",
            f"Predictions tracked: {status.predictions_tracked}",
            f"Resolved predictions: {status.resolved_predictions}",
            f"Overall accuracy: {fmt_percent(status.overall_accuracy)}",
            f"Best category: {status.best_category or 'n/a'}",
        ]
    )


def format_calibration_summary(summary: CalibrationSummary) -> str:
    lines = [
        "Edge Calibration",
        "",
        f"Predictions tracked: {summary.prediction_count}",
        f"Resolved: {summary.resolved_count}",
        f"Overall accuracy: {fmt_percent(summary.overall_accuracy)}",
        "",
        "By category:",
        "",
    ]
    labels = {
        "sports": "Sports",
        "politics": "Politics",
        "crypto": "Crypto",
        "macro": "Macro",
        "other": "Other",
    }
    for category in ("sports", "politics", "crypto", "macro", "other"):
        metrics = summary.by_category[category]
        lines.append(f"{labels[category]}: {fmt_percent(metrics.win_rate)}")
    lines.extend(
        [
            "",
            f"Best category: {labels.get(summary.best_category or '', summary.best_category or 'n/a')}",
            f"Worst category: {labels.get(summary.worst_category or '', summary.worst_category or 'n/a')}",
            "",
            "Analysis-only. Not financial advice. No automated trading.",
        ]
    )
    return "\n".join(lines)


def format_watch_confirmation(watch: WatchedMarket, market: MarketData) -> str:
    classification = classify_market(market)
    return (
        "Watching market:\n"
        f"#{watch.id} {market.title}\n"
        f"YES: {fmt_price(market.yes_price)} | Liquidity: {fmt_number(market.liquidity)}\n"
        f"Type: {classification.display}"
    )


def format_watchlist(
    watches: list[WatchedMarket],
    latest: dict[int, MarketData | None],
) -> str:
    if not watches:
        return "Your watchlist is empty. Add one with /watch <market_url_or_slug>."
    lines = ["Watchlist", ""]
    for watch in watches:
        snapshot = latest.get(watch.id)
        price = fmt_price(snapshot.yes_price) if snapshot else "n/a"
        checked = fmt_date(snapshot.updated_at) if snapshot else "never"
        type_text = classify_market(snapshot).display if snapshot else "n/a"
        lines.append(
            f"#{watch.id} {watch.title}\n"
            f"   YES: {price} | Last checked: {checked} | Type: {type_text}"
        )
    return "\n".join(lines)


def format_unwatch_result(removed: bool, watch_id: int) -> str:
    if removed:
        return f"Removed market #{watch_id} from the watchlist."
    return f"No active watched market found for id #{watch_id}."


def _format_score_components(components: dict[str, int]) -> str:
    drivers = [
        (name.replace("_", " "), score)
        for name, score in sorted(components.items(), key=lambda item: item[1], reverse=True)
        if score > 0
    ][:3]
    penalties = [
        (name.replace("_", " "), score)
        for name, score in sorted(components.items(), key=lambda item: item[1])
        if score < 0
    ][:3]
    parts = [f"{name} +{score}" for name, score in drivers]
    parts.extend(f"{name} {score}" for name, score in penalties)
    return ", ".join(parts) if parts else "none"


def _quick_reason(classification: MarketClassification) -> str:
    if not classification.reasons:
        return "standard market profile"
    return classification.reasons[0].rstrip(".")


def format_forecast_message(
    market: MarketData,
    signal: ScoreBreakdown,
    risk: ScoreBreakdown,
    forecast: AIForecast,
    classification: MarketClassification | None = None,
    movement: SnapshotMovement | None = None,
    catalyst: CatalystLike | None = None,
    *,
    edge_threshold: int = 65,
    external_context: str | None = None,
) -> str:
    classification = classification or classify_market(market, signal_score=signal.total)
    verdict = determine_verdict(signal.total, risk.total, classification.labels)
    movement_text = format_snapshot_movement(movement) if movement else "Not enough history yet."
    edge_text = _edge_text(signal, forecast, edge_threshold)
    notes = market_type_notes(classification)
    risks = _forecast_risks(forecast, classification)
    lines = [
        "Forecast",
        market.title,
        "",
        f"Verdict: {verdict_label(verdict)}",
        f"Market type: {classification.display}",
        "",
        "Why this type matters:",
        *[f"- {item}" for item in notes[:4]],
        "",
        "Market data",
        f"YES price: {fmt_price(market.yes_price)}",
        f"NO price: {fmt_price(market.no_price)}",
        f"Volume: {fmt_number(market.volume)}",
        f"Liquidity: {fmt_number(market.liquidity)}",
        f"Spread: {fmt_price(market.spread)}",
        f"End date: {fmt_date(market.end_date)}",
        "",
        "Movement",
        movement_text,
        "",
        format_catalyst_check(catalyst),
    ]
    # V2.4.3: Show external context (Wikipedia/CoinGecko/FRED) in Telegram message
    # so user can see what real-world data was fed to AI.
    if external_context and external_context.strip():
        lines.append("")
        lines.append("External context")
        lines.append(external_context.strip())
    lines.extend([
        "",
        "Scores",
        f"Signal score: {signal.total}/100",
        f"Risk score: {risk.total}/100 ({risk_label(risk.total)})",
        f"Signal drivers: {_format_score_components(signal.components)}",
        f"Risk drivers: {_format_score_components(risk.components)}",
        "",
        "Edge read",
        edge_text,
        "",
        "AI forecast",
        f"Fair probability range: {forecast.fair_probability_range}",
        f"Confidence: {forecast.confidence}",
        forecast.summary,
        "",
        "Reasons:",
        *[f"- {item}" for item in forecast.why_interesting[:4]],
        "",
        "Risks:",
        *[f"- {item}" for item in risks[:6]],
        "",
        "Analysis-only. Not financial advice. No automated trading. No guaranteed profit.",
    ])
    return "\n".join(lines)


def format_catalyst_check(catalyst: CatalystLike | None) -> str:
    if catalyst is None:
        return "\n".join(
            [
                "Catalyst check",
                "External news scanner is not configured. Using market data only.",
            ]
        )
    if isinstance(catalyst, NewsScanResult):
        return _format_news_scan_check(catalyst)
    if catalyst.scanner_not_configured:
        return "\n".join(
            [
                "Catalyst check",
                "External news scanner is not configured. Using market data only.",
            ]
        )
    notes = catalyst.notes or ["No notable catalyst notes."]
    lines = [
        "Catalyst check",
        f"Fresh catalyst: {catalyst.fresh_catalyst}",
        f"Possible catalyst: {catalyst.possible_catalyst}",
        f"Source confidence: {catalyst.source_confidence}",
        f"Market reaction: {catalyst.market_reaction}",
        "Notes:",
        *[f"- {item}" for item in notes[:4]],
    ]
    if catalyst.warnings:
        lines.extend(["Warnings:", *[f"- {item}" for item in catalyst.warnings[:3]]])
    return "\n".join(lines)


def _format_news_scan_check(scan: NewsScanResult) -> str:
    if scan.provider == "none":
        return "\n".join(
            [
                "Catalyst check",
                "External news scanner is not configured. Using market data only.",
            ]
        )
    if not scan.news_found:
        return "\n".join(
            [
                "Catalyst check",
                "",
                "No relevant external catalysts found",
                "during the selected lookback window.",
            ]
        )
    return "\n".join(
        [
            "Catalyst check",
            "",
            f"Catalyst score: {scan.catalyst_score}/100",
            f"Sentiment: {scan.sentiment}",
            f"Confidence: {scan.confidence}%",
            "",
            "Recent catalysts",
            *[f"- {item.title}" for item in scan.items[:3]],
        ]
    )


def format_catalyst_message(market: MarketData, catalyst: CatalystLike) -> str:
    if isinstance(catalyst, NewsScanResult):
        if catalyst.provider == "none":
            return "\n".join(
                [
                    "Catalyst scan",
                    "",
                    "Market:",
                    market.title,
                    "",
                    "External news scanner is not configured. Using market data only.",
                    "",
                    "Analysis-only. Not financial advice. No automated trading.",
                ]
            )
        lines = [
            "Catalyst scan",
            "",
            "Market:",
            market.title,
            "",
            f"Catalyst score: {catalyst.catalyst_score}/100",
            "",
            f"Sentiment: {catalyst.sentiment}",
            "",
        ]
        if catalyst.news_found:
            lines.append("Top news")
            for index, item in enumerate(catalyst.items[:5], start=1):
                lines.extend(
                    [
                        f"{index}.",
                        item.title,
                        item.source,
                        fmt_age(item.published_at),
                        "",
                    ]
                )
        else:
            lines.extend(
                [
                    "No relevant external catalysts found",
                    "during the selected lookback window.",
                    "",
                ]
            )
        lines.append("Analysis-only. Not financial advice. No automated trading.")
        return "\n".join(lines).rstrip()
    return "\n".join(
        [
            "Catalyst scan",
            market.title,
            "",
            format_catalyst_check(catalyst),
            "",
            "Analysis-only. Not financial advice. No automated trading.",
        ]
    )


def _edge_text(signal: ScoreBreakdown, forecast: AIForecast, edge_threshold: int) -> str:
    if signal.total < edge_threshold:
        return "No clear edge detected."
    return (
        f"Possible edge detected, but confidence is {forecast.confidence}. Compare the AI fair "
        f"range ({forecast.fair_probability_range}) cautiously with market price."
    )


def _forecast_risks(
    forecast: AIForecast,
    classification: MarketClassification,
) -> list[str]:
    risks = list(forecast.risks)
    if classification.has(MarketType.MEME):
        risks.append("Meme framing can create attention-driven volume without reliable signal.")
    if classification.has(MarketType.LOTTERY):
        risks.append("Lottery-style price means small probability errors can dominate the outcome.")
    if classification.has(MarketType.EXTREME_PROBABILITY):
        risks.append("Extreme-tail market; small absolute price changes can look large.")
    if classification.has(MarketType.AMBIGUOUS_RESOLUTION):
        risks.append("Ambiguous resolution language can make final settlement harder to reason about.")
    if classification.has(MarketType.LOW_LIQUIDITY):
        risks.append("Low liquidity can make displayed prices easier to move and less informative.")
    if classification.has(MarketType.HIGH_VOLUME_NO_EDGE):
        risks.append("High volume does not itself imply edge; the market may be efficiently priced.")
    return risks


def format_alert_message(
    market: MarketData,
    signal: ScoreBreakdown,
    risk: ScoreBreakdown,
) -> str:
    classification = classify_market(market, signal_score=signal.total)
    verdict = determine_verdict(signal.total, risk.total, classification.labels)
    return (
        f"Signal alert: {verdict_label(verdict)}\n"
        f"{market.title}\n\n"
        f"Type: {classification.display}\n"
        f"Signal score: {signal.total}/100 | Risk score: {risk.total}/100\n"
        f"YES: {fmt_price(market.yes_price)} | Spread: {fmt_price(market.spread)}\n"
        f"Volume: {fmt_number(market.volume)} | Liquidity: {fmt_number(market.liquidity)}\n\n"
        "Analysis-only. Review risks before making any personal decision."
    )


def format_daily_digest(
    strong_watched: list[MarketAnalysisRow],
    interesting_watched: list[MarketAnalysisRow],
    high_volume_active: list[MarketAnalysisRow],
    avoid_risky: list[MarketAnalysisRow],
    ignore_markets: list[MarketAnalysisRow],
    catalyst_notes: dict[str, str] | None = None,
    strong_catalyst_markets: list[tuple[MarketData, NewsScanResult]] | None = None,
    top_opportunities: list[OpportunityCandidate] | None = None,
    calibration_summary: CalibrationSummary | None = None,
    alpha_report: AlphaReport | None = None,
) -> str:
    lines = ["Daily Polymarket digest", ""]
    if alpha_report:
        lines.extend(_format_daily_alpha(alpha_report))
        lines.append("")

    lines.append("Strong watched signals")
    if strong_watched:
        for index, row in enumerate(strong_watched[:3], start=1):
            lines.append(_format_daily_row(index, row, catalyst_notes))
    else:
        lines.append("No strong watched signals right now.")

    lines.extend(["", "Interesting watched markets"])
    if interesting_watched:
        for index, row in enumerate(interesting_watched[:3], start=1):
            lines.append(_format_daily_row(index, row, catalyst_notes))
    else:
        lines.append("No interesting watched markets right now.")

    lines.extend(["", "High-volume active markets"])
    if high_volume_active:
        for index, row in enumerate(high_volume_active[:3], start=1):
            lines.append(_format_daily_row(index, row, catalyst_notes))
    else:
        lines.append("No high-volume active markets passed filters right now.")

    lines.extend(["", "Avoid / risky markets"])
    if avoid_risky:
        for index, row in enumerate(avoid_risky[:3], start=1):
            lines.append(_format_daily_row(index, row, catalyst_notes))
    else:
        lines.append("No high-risk markets found in this pass.")

    lines.extend(["", "Meme / lottery markets to ignore"])
    if ignore_markets:
        for index, row in enumerate(ignore_markets[:3], start=1):
            lines.append(_format_daily_row(index, row, catalyst_notes))
    else:
        lines.append("No obvious meme or lottery markets in this pass.")

    lines.extend(["", "Strong catalyst markets"])
    if strong_catalyst_markets:
        for index, (market, scan) in enumerate(strong_catalyst_markets[:3], start=1):
            lines.append(
                f"{index}. {market.title}\n"
                f"   Catalyst score: {scan.catalyst_score}/100 | "
                f"Sentiment: {scan.sentiment} | Confidence: {scan.confidence}%"
            )
    else:
        lines.append("No strong external catalysts found in this pass.")

    lines.extend(["", "Top opportunities today"])
    if top_opportunities:
        for index, item in enumerate(top_opportunities[:3], start=1):
            lines.append(
                f"{index}. {item.question}\n"
                f"   Opportunity: {item.opportunity_score}/100 | Edge: {_fmt_edge_mid(item)} | "
                f"Risk: {item.risk_score}/100"
            )
    else:
        lines.append("No qualified opportunities found in this pass.")

    lines.extend(["", "Model performance"])
    if calibration_summary:
        lines.extend(_format_model_performance(calibration_summary))
    else:
        lines.append("No calibration history yet.")

    lines.extend(["", "Analysis only. Not financial advice. No automated trading."])
    return "\n".join(lines)


def _format_daily_alpha(report: AlphaReport) -> list[str]:
    top = report.top_opportunity.question if report.top_opportunity else "none"
    catalyst = report.strongest_catalyst.question if report.strongest_catalyst else "none"
    alert = report.watchlist_alert.market.title if report.watchlist_alert else "none"
    return [
        "Today's Alpha",
        f"Top opportunity: {top}",
        f"Strongest catalyst: {catalyst}",
        f"Watchlist alert: {alert}",
    ]


def _format_model_performance(summary: CalibrationSummary) -> list[str]:
    lines = [
        f"Predictions tracked: {summary.prediction_count}",
        f"Resolved: {summary.resolved_count}",
        f"Accuracy: {fmt_percent(summary.overall_accuracy)}",
        "",
        f"Best category: {_category_label(summary.best_category)}",
        f"Worst category: {_category_label(summary.worst_category)}",
    ]
    if summary.best_category and summary.best_category in summary.by_category:
        best = summary.by_category[summary.best_category]
        lines[-2] = f"Best category: {_category_label(summary.best_category)} ({fmt_percent(best.win_rate)})"
    if summary.worst_category and summary.worst_category in summary.by_category:
        worst = summary.by_category[summary.worst_category]
        lines[-1] = f"Worst category: {_category_label(summary.worst_category)} ({fmt_percent(worst.win_rate)})"
    return lines


def _category_label(category: str | None) -> str:
    if not category:
        return "n/a"
    return category.capitalize()


def _format_daily_row(
    index: int,
    row: MarketAnalysisRow,
    catalyst_notes: dict[str, str] | None = None,
) -> str:
    market, signal, risk, classification = row
    verdict = determine_verdict(signal.total, risk.total, classification.labels)
    text = (
        f"{index}. {verdict_label(verdict)} - {market.title}\n"
        f"   Type: {classification.display} | Signal: {signal.total}/100 | "
        f"Risk: {risk.total}/100 | YES: {fmt_price(market.yes_price)} | "
        f"Vol: {fmt_number(market.volume)}"
    )
    note = (catalyst_notes or {}).get(_market_key(market))
    if note:
        text = f"{text}\n   Catalyst: {note}"
    return text


def _market_key(market: MarketData) -> str:
    return market.slug or market.market_id


def format_signal_history(records: list[SignalHistoryRecord]) -> str:
    if not records:
        return "No generated signals yet."
    lines = ["Recent signals", ""]
    for record in records[:10]:
        lines.append(
            f"#{record.id} {record.market_title}\n"
            f"   Signal: {record.signal_score}/100 | Risk: {record.risk_score}/100 | "
            f"Verdict: {record.verdict} | Created: {fmt_date(record.created_at)}"
        )
    return "\n".join(lines)
