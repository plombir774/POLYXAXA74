from datetime import UTC, datetime

from app.analysis.classification import classify_market
from app.analysis.movement import SnapshotMovement
from app.bot.messages import (
    format_catalyst_message,
    format_daily_digest,
    format_forecast_message,
    market_not_found_message,
)
from app.news.schemas import CatalystAnalysis
from app.news.models import NewsItem, NewsScanResult
from app.polymarket.schemas import AIForecast, MarketData, ScoreBreakdown


def test_invalid_slug_friendly_error_message() -> None:
    assert (
        market_not_found_message()
        == "I could not find an active Polymarket market for this slug or URL."
    )


def make_market(slug: str, title: str, yes_price: float = 0.55) -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=title,
        url=f"https://polymarket.com/event/{slug}",
        yes_price=yes_price,
        no_price=1 - yes_price,
        volume=100_000,
        liquidity=50_000,
        spread=0.02,
        raw={},
    )


def score(total: int) -> ScoreBreakdown:
    return ScoreBreakdown(total=total, components={"test": total}, reason="test")


def row(slug: str, title: str, signal_total: int, risk_total: int, yes_price: float = 0.55):
    market = make_market(slug, title, yes_price)
    signal = score(signal_total)
    risk = score(risk_total)
    classification = classify_market(market, signal_score=signal.total)
    return (market, signal, risk, classification)


def test_daily_digest_builder_includes_v2_sections_and_disclaimer() -> None:
    strong = [row("watched", "Watched signal", 82, 35)]
    interesting = [row("interesting", "Interesting watched market", 60, 45)]
    active = [row("active", "Active idea", 70, 30)]
    risky = [row("risky", "Risky market", 40, 85)]
    ignore = [row("meme", "Will something happen before GTA VI?", 35, 60)]

    message = format_daily_digest(
        strong,
        interesting,
        active,
        risky,
        ignore,
        {"watched": "scanner not configured"},
    )

    assert "Strong watched signals" in message
    assert "Interesting watched markets" in message
    assert "High-volume active markets" in message
    assert "Avoid / risky markets" in message
    assert "Meme / lottery markets to ignore" in message
    assert "STRONG SIGNAL - Watched signal" in message
    assert "Catalyst: scanner not configured" in message
    assert "Analysis only. Not financial advice. No automated trading." in message


def test_daily_digest_with_no_strong_signals_uses_clear_wording() -> None:
    message = format_daily_digest([], [], [], [], [])
    assert "No strong watched signals right now." in message
    assert "strongest signals" not in message.lower()


def test_forecast_message_includes_market_type() -> None:
    market = make_market("meme", "Will this happen before GTA VI?", 0.04)
    signal = score(45)
    risk = score(60)
    forecast = AIForecast(
        fair_probability_range="3-6%",
        summary="Low-confidence analysis.",
        why_interesting=["High attention market."],
        risks=["Thin edge."],
        verdict="WATCH",
        confidence="low",
    )

    message = format_forecast_message(
        market,
        signal,
        risk,
        forecast,
        movement=SnapshotMovement(change_1h=None, change_6h=None, change_24h=None),
    )

    assert "Market type:" in message
    assert "MEME" in message
    assert "Catalyst check" in message
    assert "No clear edge detected." in message


def test_forecast_message_includes_movement_section() -> None:
    market = make_market("normal", "Will the Fed cut rates?", 0.55)
    message = format_forecast_message(
        market,
        score(75),
        score(30),
        AIForecast(
            fair_probability_range="52-58%",
            summary="Cautious summary.",
            why_interesting=["Movement exists."],
            risks=["Data can be stale."],
            verdict="WATCH",
            confidence="medium",
        ),
        movement=SnapshotMovement(change_1h=0.01, change_6h=0.02, change_24h=0.03),
    )

    assert "Movement" in message
    assert "1h: +1.0%" in message
    assert "6h: +2.0%" in message
    assert "24h: +3.0%" in message
    assert "Edge read" in message


def test_forecast_message_includes_real_news_catalyst_section() -> None:
    market = make_market("btc", "Will Bitcoin hit $150k by June 30, 2026?", 0.55)
    scan = NewsScanResult(
        news_found=True,
        items=[
            NewsItem(
                title="SEC ETF approval discussion",
                url="https://example.com/sec",
                source="Reuters",
                published_at=datetime.now(UTC),
                summary="ETF inflows surge.",
            )
        ],
        sentiment="bullish",
        confidence=82,
        catalyst_score=78,
        summary="Strong catalyst backdrop.",
        provider="rss",
        query="Bitcoin BTC crypto market",
    )

    message = format_forecast_message(
        market,
        score(75),
        score(30),
        AIForecast(
            fair_probability_range="52-58%",
            summary="Cautious summary.",
            why_interesting=["Catalyst exists."],
            risks=["Data can be stale."],
            verdict="WATCH",
            confidence="medium",
        ),
        catalyst=scan,
    )

    assert "Catalyst score: 78/100" in message
    assert "Sentiment: bullish" in message
    assert "SEC ETF approval discussion" in message


def test_daily_digest_includes_strong_catalyst_markets() -> None:
    market = make_market("btc", "Bitcoin 150k", 0.55)
    scan = NewsScanResult(
        news_found=True,
        catalyst_score=82,
        sentiment="bullish",
        confidence=80,
        summary="Strong catalyst backdrop.",
        provider="rss",
    )

    message = format_daily_digest([], [], [], [], [], strong_catalyst_markets=[(market, scan)])

    assert "Strong catalyst markets" in message
    assert "Bitcoin 150k" in message
    assert "Catalyst score: 82/100" in message


def test_catalyst_message_provider_none_is_friendly() -> None:
    market = make_market("fed", "Will the Fed cut rates?", 0.55)
    catalyst = CatalystAnalysis(
        scanner_not_configured=True,
        notes=["External news scanner is not configured. Using market data only."],
    )

    message = format_catalyst_message(market, catalyst)

    assert "Catalyst scan" in message
    assert "External news scanner is not configured. Using market data only." in message


def test_openai_failure_fallback_message_is_user_friendly() -> None:
    from app.analysis.forecast import deterministic_forecast_fallback

    fallback = deterministic_forecast_fallback(score(45), score(30), "OpenAI API request failed")
    assert (
        fallback.summary
        == "AI analysis is temporarily unavailable, using deterministic analysis only."
    )
    assert "OpenAI API request failed" not in fallback.summary
