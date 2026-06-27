from __future__ import annotations

from datetime import UTC, datetime

from app.alpha.models import (
    AlphaCatalyst,
    AlphaMarketMovement,
    AlphaReport,
    AlphaRiskMarket,
    AlphaWatchlistCandidate,
    MarketMover,
    WatchlistAlert,
)
from app.alpha.ranking import calculate_alpha_score, rank_alpha_opportunities
from app.analysis.classification import MarketType
from app.analysis.scoring import determine_verdict
from app.news.models import NewsScanResult
from app.opportunity.history import CalibrationSummary
from app.opportunity.models import OpportunityCandidate


class AlphaEngine:
    def build_report(
        self,
        *,
        opportunities: list[OpportunityCandidate],
        catalysts: dict[str, NewsScanResult] | None = None,
        watchlist: list[AlphaWatchlistCandidate] | None = None,
        movements: list[AlphaMarketMovement] | None = None,
        risk_markets: list[AlphaRiskMarket] | None = None,
        calibration_summary: CalibrationSummary | None = None,
        category: str = "all",
        generated_at: datetime | None = None,
    ) -> AlphaReport:
        catalysts = catalysts or {}
        watchlist = watchlist or []
        movements = movements or []
        generated_at = generated_at or datetime.now(UTC)
        ranked = rank_alpha_opportunities(
            opportunities,
            catalysts=catalysts,
            calibration_summary=calibration_summary,
            limit=10,
        )
        top = ranked[0] if ranked else None
        top_score = (
            calculate_alpha_score(
                top,
                catalyst=_candidate_catalyst(top, catalysts),
                calibration_summary=calibration_summary,
            )
            if top
            else 0
        )
        return AlphaReport(
            generated_at=generated_at,
            category=category,
            alpha_score=top_score,
            top_opportunity=top,
            strongest_catalyst=_select_strongest_catalyst(ranked, watchlist, catalysts),
            watchlist_alert=_select_watchlist_alert(watchlist),
            upward_movers=_select_movers(movements, upward=True),
            downward_movers=_select_movers(movements, upward=False),
            highest_risk_market=_select_highest_risk(watchlist, risk_markets or []),
            calibration_summary=calibration_summary,
        )


def _select_strongest_catalyst(
    opportunities: list[OpportunityCandidate],
    watchlist: list[AlphaWatchlistCandidate],
    catalysts: dict[str, NewsScanResult],
) -> AlphaCatalyst | None:
    questions: dict[str, str] = {}
    for candidate in opportunities:
        for key in (candidate.market_slug, candidate.market_id, candidate.question):
            if key:
                questions[key] = candidate.question
    for row in watchlist:
        for key in (row.market.slug, row.market.market_id, row.market.title):
            if key:
                questions[key] = row.market.title
    ranked = []
    for key, scan in catalysts.items():
        if not scan.news_found or scan.catalyst_score <= 0:
            continue
        ranked.append((_news_rank(scan), key, scan))
    if not ranked:
        return None
    _rank, key, scan = max(ranked, key=lambda item: item[0])
    return AlphaCatalyst(
        market_id=key,
        question=questions.get(key, key),
        catalyst_score=scan.catalyst_score,
        sentiment=scan.sentiment,
        confidence=scan.confidence,
        explanation=_catalyst_explanation(scan),
    )


def _select_watchlist_alert(
    watchlist: list[AlphaWatchlistCandidate],
) -> WatchlistAlert | None:
    if not watchlist:
        return None
    row = max(
        watchlist,
        key=lambda item: (
            item.signal.total,
            item.catalyst.catalyst_score if item.catalyst and item.catalyst.news_found else 0,
            -item.risk.total,
        ),
    )
    verdict = determine_verdict(row.signal.total, row.risk.total, row.classification.labels)
    return WatchlistAlert(
        market=row.market,
        signal_score=row.signal.total,
        catalyst_score=row.catalyst.catalyst_score if row.catalyst and row.catalyst.news_found else 0,
        risk_score=row.risk.total,
        verdict=verdict,
    )


def _select_movers(
    movements: list[AlphaMarketMovement],
    *,
    upward: bool,
) -> list[MarketMover]:
    movers = [
        MarketMover(
            market=item.market,
            change_24h=item.movement.change_24h,
            current_probability=item.market.yes_price,
        )
        for item in movements
        if item.movement.change_24h is not None
        and ((item.movement.change_24h > 0) if upward else (item.movement.change_24h < 0))
    ]
    return sorted(movers, key=lambda item: abs(item.change_24h), reverse=True)[:3]


def _select_highest_risk(
    watchlist: list[AlphaWatchlistCandidate],
    risk_markets: list[AlphaRiskMarket],
) -> AlphaRiskMarket | None:
    risks = list(risk_markets)
    for row in watchlist:
        if row.classification.has(MarketType.MEME):
            continue
        reason = row.classification.reasons[0] if row.classification.reasons else row.risk.reason
        risks.append(
            AlphaRiskMarket(
                market_id=row.market.slug or row.market.market_id,
                question=row.market.title,
                risk_score=row.risk.total,
                reason=reason,
            )
        )
    if not risks:
        return None
    return max(risks, key=lambda item: item.risk_score)


def _candidate_catalyst(
    candidate: OpportunityCandidate,
    catalysts: dict[str, NewsScanResult],
) -> NewsScanResult | None:
    for key in (candidate.market_slug, candidate.market_id, candidate.question):
        if key and key in catalysts:
            return catalysts[key]
    return None


def _news_rank(scan: NewsScanResult) -> tuple[int, int, int]:
    return (scan.catalyst_score, scan.confidence, len(scan.items))


def _catalyst_explanation(scan: NewsScanResult) -> str:
    if scan.summary:
        return scan.summary
    if scan.items:
        return scan.items[0].title
    return "Relevant external catalyst detected."
