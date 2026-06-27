from datetime import UTC, datetime

from app.bot.messages import format_status_message
from app.bot.status import build_status_data
from app.config import Settings
from app.db.repository import MarketRepository
from app.polymarket.schemas import MarketData


def make_settings(openai_api_key: str | None = "sk-test") -> Settings:
    return Settings(
        telegram_bot_token="token",
        telegram_allowed_user_id=123,
        openai_api_key=openai_api_key,
        openai_model="gpt-5.5",
        news_provider="none",
        news_api_key=None,
        news_lookback_hours=24,
        news_max_results=5,
        database_url="sqlite:///:memory:",
        check_interval_minutes=30,
        min_signal_score=75,
        meme_allow_volume_threshold=10_000_000,
        polymarket_gamma_base_url="https://gamma-api.polymarket.com",
        polymarket_clob_base_url="https://clob.polymarket.com",
        request_timeout_seconds=15.0,
        polymarket_max_retries=2,
        polymarket_retry_backoff_seconds=0.6,
        fred_api_key=None,
        dune_api_key=None,
    )


def make_market(slug: str) -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=f"Market {slug}",
        url=f"https://polymarket.com/event/{slug}",
        raw={},
    )


def test_status_data_builder(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    repository.add_watch(make_market("one"))
    repository.add_watch(make_market("two"))
    last_check = datetime(2026, 6, 10, 12, 30, tzinfo=UTC)

    data = build_status_data(
        make_settings(openai_api_key=None),
        repository,
        {
            "last_scheduled_check_time": last_check,
            "polymarket_api_last_status": "ok",
        },
    )

    assert data.running is True
    assert data.database_connected is True
    assert data.watchlist_count == 2
    assert data.last_scheduled_check_time == last_check
    assert data.openai_configured is False
    assert data.news_provider == "none"
    assert data.news_scanner_configured is False
    assert data.news_lookback_hours == 24
    assert data.news_max_results == 5
    assert data.polymarket_api_last_status == "ok"
    assert data.check_interval_minutes == 30
    assert data.min_signal_score == 75

    message = format_status_message(data)
    assert "News provider: none" in message
    assert "News scanner configured: no" in message
    assert "News lookback hours: 24" in message
    assert "News max results: 5" in message
    assert "Opportunity engine: enabled" in message
    assert "Last edge scan: n/a" in message
    assert "Markets scanned: 0" in message
    assert "Qualified opportunities: 0" in message


def test_status_reports_rss_fallback_when_newsapi_key_missing(tmp_path) -> None:
    repository = MarketRepository(str(tmp_path / "bot.db"))
    repository.init_schema()
    settings = make_settings(openai_api_key=None)
    settings = Settings(
        **{
            **settings.__dict__,
            "news_provider": "newsapi",
            "news_api_key": None,
        }
    )

    data = build_status_data(settings, repository, {})

    assert data.news_provider == "rss"
    assert data.news_scanner_configured is True
