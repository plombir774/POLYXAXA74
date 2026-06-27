from __future__ import annotations

import re
from datetime import UTC, datetime

from app.analysis.classification import MarketClassification, classify_market
from app.news.client import NewsClient
from app.news.queries import (
    CRYPTO_TERMS,
    MACRO_TERMS,
    POLITICS_TERMS,
    SPORTS_TERMS,
    build_catalyst_queries,
)
from app.news.schemas import CatalystAnalysis, CatalystRelevance, NewsSearchResponse, NewsSearchResult
from app.polymarket.schemas import MarketData


SOURCE_QUALITY_HINTS = (
    "reuters",
    "ap",
    "associated press",
    "bloomberg",
    "cnbc",
    "bbc",
    "espn",
    "official",
    "federal reserve",
    "sec",
)


async def analyze_market_catalysts(
    market: MarketData,
    news_client: NewsClient,
    classification: MarketClassification | None = None,
) -> CatalystAnalysis:
    classification = classification or classify_market(market)
    queries = build_catalyst_queries(market, classification)
    response = await news_client.search(queries)
    return analyze_catalysts_from_response(market, queries, response, classification)


def analyze_catalysts_from_response(
    market: MarketData,
    queries: list[str],
    response: NewsSearchResponse,
    classification: MarketClassification | None = None,
) -> CatalystAnalysis:
    classification = classification or classify_market(market)
    if response.scanner_not_configured:
        return CatalystAnalysis(
            scanner_not_configured=True,
            fresh_catalyst="unknown",
            possible_catalyst="scanner not configured",
            source_confidence="low",
            market_reaction="unclear",
            notes=["External news scanner is not configured. Using market data only."],
            warnings=["No external news/search results were checked."],
            queries=queries,
        )

    if response.error and not response.results:
        return CatalystAnalysis(
            fresh_catalyst="unknown",
            possible_catalyst="none detected",
            source_confidence="low",
            market_reaction="unclear",
            notes=[f"News scanner returned no usable results: {response.error}"],
            warnings=["Treat catalyst analysis as missing, not as proof there is no catalyst."],
            queries=queries,
            results=response.results,
        )

    if not response.results:
        return CatalystAnalysis(
            fresh_catalyst="no",
            possible_catalyst="none detected",
            source_confidence="low",
            market_reaction="unclear",
            notes=["No recent external catalyst was detected in the configured news search."],
            queries=queries,
        )

    scored = [
        (result, score_catalyst_relevance(market, result, classification))
        for result in response.results
    ]
    best_result, best = max(scored, key=lambda item: item[1].score)
    fresh = "yes" if best.score >= 60 else "no"
    market_reaction = "possible delayed reaction" if best.score >= 70 else "unclear"
    if _price_is_extreme(market):
        market_reaction = "already priced"
    notes = [best_result.title]
    if best_result.source:
        notes.append(f"Source: {best_result.source}")
    notes.extend(best.reasons[:3])
    warnings = list(best.warnings)
    if response.error:
        warnings.append(response.error)
    return CatalystAnalysis(
        fresh_catalyst=fresh,
        possible_catalyst=best_result.title if best.score >= 40 else "none detected",
        source_confidence=best.confidence,
        market_reaction=market_reaction,
        notes=notes,
        relevance_score=best.score,
        reasons=best.reasons,
        warnings=warnings,
        queries=queries,
        results=response.results,
    )


def score_catalyst_relevance(
    market: MarketData,
    result: NewsSearchResult,
    classification: MarketClassification | None = None,
) -> CatalystRelevance:
    classification = classification or classify_market(market)
    market_text = _normalize(" ".join([market.title, market.slug, market.description or ""]))
    result_text = _normalize(" ".join([result.title, result.snippet or "", result.source or ""]))
    market_tokens = _important_tokens(market_text)
    result_tokens = set(_important_tokens(result_text))
    overlap = [token for token in market_tokens if token in result_tokens]
    reasons: list[str] = []
    warnings: list[str] = []
    score = 0

    if overlap:
        score += min(30, len(overlap) * 8)
        reasons.append(f"Matches market entities/terms: {', '.join(overlap[:4])}.")
    else:
        warnings.append("Weak title/entity overlap with the market.")

    category_score, category_reason = _category_match_score(classification, market_text, result_text)
    score += category_score
    if category_reason:
        reasons.append(category_reason)

    if result.published_at:
        hours_old = _hours_old(result.published_at)
        if hours_old is not None and hours_old <= 24:
            score += 20
            reasons.append("Published within the recent catalyst window.")
        elif hours_old is not None and hours_old <= 72:
            score += 10
            reasons.append("Published recently, but outside the freshest window.")
        else:
            warnings.append("Article is not fresh enough to treat as a current catalyst.")
    else:
        warnings.append("Published time is missing, so recency is uncertain.")

    source_text = _normalize(result.source or "")
    if any(source in source_text for source in SOURCE_QUALITY_HINTS):
        score += 15
        reasons.append("Source quality placeholder is favorable.")
    elif result.source:
        score += 5
        reasons.append("Source is present but not quality-ranked.")
    else:
        warnings.append("Source is missing.")

    if _resolution_relevant(market_text, result_text):
        score += 20
        reasons.append("Result appears relevant to resolution criteria or outcome direction.")
    else:
        warnings.append("Resolution relevance is unclear.")

    effect = _plausible_effect(result_text)
    if effect != "unclear":
        score += 15
        reasons.append(f"Plausible effect on YES/NO price: {effect}.")
    else:
        warnings.append("No clear directional price effect detected.")

    score = max(0, min(100, score))
    return CatalystRelevance(
        score=score,
        confidence=_confidence(score),
        reasons=reasons,
        warnings=warnings,
        plausible_effect=effect,
    )


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9$%\s]", " ", value.lower())


def _important_tokens(text: str) -> list[str]:
    stop = {
        "will",
        "the",
        "and",
        "for",
        "with",
        "this",
        "that",
        "market",
        "hit",
        "win",
        "before",
        "after",
        "yes",
        "no",
    }
    tokens = [token for token in text.split() if len(token) >= 3 and token not in stop]
    return list(dict.fromkeys(tokens))


def _category_match_score(
    classification: MarketClassification,
    market_text: str,
    result_text: str,
) -> tuple[int, str | None]:
    combined = f"{market_text} {result_text}"
    if _has_any(market_text, CRYPTO_TERMS) and _has_any(result_text, CRYPTO_TERMS):
        return 15, "Category match: crypto catalyst context."
    if _has_any(market_text, POLITICS_TERMS) and _has_any(result_text, POLITICS_TERMS):
        return 15, "Category match: politics catalyst context."
    if _has_any(market_text, MACRO_TERMS) and _has_any(result_text, MACRO_TERMS):
        return 15, "Category match: macro catalyst context."
    if _has_any(market_text, SPORTS_TERMS) and _has_any(result_text, SPORTS_TERMS):
        return 15, "Category match: sports catalyst context."
    if "NEWS_DRIVEN" in classification.display and any(
        term in combined for term in CRYPTO_TERMS + POLITICS_TERMS + MACRO_TERMS + SPORTS_TERMS
    ):
        return 8, "Market is news-driven, but category match is broad."
    return 0, None


def _resolution_relevant(market_text: str, result_text: str) -> bool:
    resolution_terms = (
        "poll",
        "polling",
        "election",
        "approval",
        "fed",
        "rate",
        "cpi",
        "inflation",
        "etf",
        "sec",
        "injury",
        "lineup",
        "qualifying",
        "forecast",
        "odds",
        "ban",
        "sanction",
        "government",
    )
    return bool(set(_important_tokens(market_text)) & set(_important_tokens(result_text))) and any(
        term in result_text for term in resolution_terms
    )


def _plausible_effect(result_text: str) -> str:
    positive_terms = ("approved", "wins", "lead", "surges", "cuts", "higher odds", "qualifies")
    negative_terms = ("rejected", "loses", "trails", "falls", "injury", "ban", "sanction")
    if any(term in result_text for term in positive_terms):
        return "possible YES-positive catalyst"
    if any(term in result_text for term in negative_terms):
        return "possible YES-negative catalyst"
    return "unclear"


def _hours_old(value: datetime) -> float | None:
    published = value if value.tzinfo else value.replace(tzinfo=UTC)
    return (datetime.now(UTC) - published).total_seconds() / 3600


def _price_is_extreme(market: MarketData) -> bool:
    return market.yes_price is not None and (market.yes_price < 0.08 or market.yes_price > 0.92)


def _confidence(score: int) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)
