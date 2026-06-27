from datetime import UTC, datetime, timedelta

import pytest

from app.analysis.classification import classify_market
from app.analysis.forecast import build_forecast_payload, build_openai_forecast_request
from app.analysis.risk import calculate_risk_score
from app.analysis.scoring import calculate_signal_score
from app.news.catalyst import analyze_market_catalysts, score_catalyst_relevance
from app.news.client import NewsClient
from app.news.queries import build_catalyst_queries
from app.news.schemas import CatalystAnalysis, NewsSearchResult
from app.polymarket.schemas import MarketData


def market(slug: str, title: str, description: str = "") -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=title,
        url=f"https://polymarket.com/event/{slug}",
        description=description,
        yes_price=0.55,
        no_price=0.45,
        volume=500_000,
        liquidity=100_000,
        spread=0.02,
        raw={},
    )


@pytest.mark.asyncio
async def test_news_provider_none_returns_scanner_not_configured() -> None:
    news_client = NewsClient(provider="none")
    analysis = await analyze_market_catalysts(
        market("btc", "Will Bitcoin hit $150k by June 30, 2026?"),
        news_client,
    )

    assert analysis.scanner_not_configured is True
    assert "External news scanner is not configured" in analysis.notes[0]


def test_query_builder_crypto_btc() -> None:
    queries = build_catalyst_queries(market("btc", "Will Bitcoin hit $150k by June 30, 2026?"))
    joined = " ".join(queries)
    assert "BTC" in joined
    assert "ETF" in joined
    assert "SEC" in joined


def test_query_builder_politics() -> None:
    queries = build_catalyst_queries(market("trump", "Will Trump win the 2028 election?"))
    joined = " ".join(queries).lower()
    assert "polling" in joined
    assert "election" in joined
    assert "2028" in joined


def test_query_builder_macro() -> None:
    queries = build_catalyst_queries(market("fed", "Will Fed cut rates in July?"))
    joined = " ".join(queries)
    assert "Federal Reserve" in joined
    assert "CPI" in joined
    assert "rates" in joined


def test_query_builder_sports() -> None:
    queries = build_catalyst_queries(
        market("world-cup", "Will USA win the 2026 FIFA World Cup?")
    )
    joined = " ".join(queries)
    assert "FIFA World Cup" in joined
    assert "injury" in joined
    assert "odds" in joined


def test_catalyst_relevance_scoring_high_for_fresh_matching_result() -> None:
    item = NewsSearchResult(
        title="Bitcoin ETF optimism surges after SEC approval signal",
        source="Reuters",
        published_at=datetime.now(UTC) - timedelta(hours=2),
        snippet="BTC traders watch ETF approval and Fed rates as Bitcoin odds move higher.",
    )
    relevance = score_catalyst_relevance(
        market("btc", "Will Bitcoin hit $150k by June 30, 2026?"),
        item,
    )

    assert relevance.score >= 70
    assert relevance.confidence == "high"
    assert relevance.reasons


def test_forecast_payload_includes_catalyst_context() -> None:
    item = market("fed", "Will Fed cut rates in July?")
    signal = calculate_signal_score(item)
    risk = calculate_risk_score(item)
    catalyst = CatalystAnalysis(
        fresh_catalyst="yes",
        possible_catalyst="Fed officials signal a July cut",
        source_confidence="medium",
        market_reaction="unclear",
        notes=["Fed officials signal a July cut"],
    )

    payload = build_forecast_payload(item, signal, risk, catalyst=catalyst)

    assert payload["catalyst_context"]["fresh_catalyst"] == "yes"
    assert payload["catalyst_context"]["possible_catalyst"] == "Fed officials signal a July cut"

    body = build_openai_forecast_request("gpt-test", payload)
    user_prompt = body["input"][1]["content"][0]["text"]
    assert "catalyst context" in user_prompt
    assert "Fed officials signal a July cut" in user_prompt


def test_forecast_payload_does_not_claim_verified_news_when_scanner_off() -> None:
    item = market("fed", "Will Fed cut rates in July?")
    payload = build_forecast_payload(
        item,
        calculate_signal_score(item),
        calculate_risk_score(item),
    )

    assert payload["catalyst_context"]["scanner_not_configured"] is True
    assert "Using market data only" in payload["catalyst_context"]["notes"][0]

    body = build_openai_forecast_request("gpt-test", payload)
    system_prompt = body["input"][0]["content"][0]["text"]
    user_prompt = body["input"][1]["content"][0]["text"]
    assert "scanner is not configured" in system_prompt
    assert "verified catalysts" in system_prompt
    assert '"scanner_not_configured": true' in user_prompt
