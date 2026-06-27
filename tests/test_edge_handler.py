import logging
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.news.models import NewsScanResult
from app.polymarket.schemas import MarketData


telegram_module = types.ModuleType("telegram")
telegram_module.Update = object
telegram_ext_module = types.ModuleType("telegram.ext")
telegram_ext_module.Application = object
telegram_ext_module.CommandHandler = object
telegram_ext_module.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
sys.modules.setdefault("telegram", telegram_module)
sys.modules.setdefault("telegram.ext", telegram_ext_module)

from app.bot.handlers import edge


def market(slug: str, title: str, volume: float = 1_000_000) -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=title,
        url=f"https://polymarket.com/event/{slug}",
        description="A clear test market.",
        yes_price=0.45,
        no_price=0.55,
        volume=volume,
        volume_24hr=100_000,
        liquidity=200_000,
        spread=0.01,
        end_date=datetime.now(UTC) + timedelta(days=60),
        active=True,
        raw={},
    )


class FakeMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUser:
    id = 123


class FakeUpdate:
    def __init__(self, text: str) -> None:
        self.effective_user = FakeUser()
        self.effective_message = FakeMessage(text)


class FakeRepository:
    def list_watches(self):
        return []

    def get_latest_snapshot(self, watch_id):
        return None


class FakePolymarketClient:
    def __init__(self) -> None:
        self.last_limit: int | None = None

    async def fetch_active_markets(self, limit: int = 10) -> list[MarketData]:
        self.last_limit = limit
        return [
            market("btc", "Will Bitcoin hit $150k by June 30, 2026?", 5_000_000),
            market("fed", "Will Fed cut rates in July?", 2_000_000),
            market("trump", "Will Trump win the 2028 election?", 2_000_000),
            market("fifa", "Will USA win the 2026 FIFA World Cup?", 4_000_000),
            market("nba", "Will the Lakers win the NBA Finals?", 3_000_000),
            market("ufc", "Will fighter A win UFC 320?", 2_500_000),
            market("tennis", "Will Player B win Wimbledon tennis?", 2_200_000),
            market("esports", "Will Global Esports win Masters London?", 2_100_000),
            market("boxing", "Will boxer C win the boxing title?", 2_000_000),
        ]


class FakeNewsClient:
    configured = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, bool]] = []

    async def scan_market(
        self,
        market_title: str,
        market_type: str,
        *,
        use_openai: bool = True,
    ) -> NewsScanResult:
        self.calls.append((market_title, market_type, use_openai))
        return NewsScanResult(
            news_found=False,
            catalyst_score=0,
            sentiment="neutral",
            confidence=0,
            provider="fake",
        )


class FakeApplication:
    def __init__(self, client: FakePolymarketClient, news_client: FakeNewsClient) -> None:
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_id=123,
                openai_api_key=None,
                openai_model="gpt-5.5",
                news_provider="newsapi",
                news_api_key=None,
                news_lookback_hours=24,
                news_max_results=5,
                database_url="sqlite:///:memory:",
                check_interval_minutes=60,
                min_signal_score=70,
                meme_allow_volume_threshold=10_000_000,
                polymarket_gamma_base_url="https://gamma-api.polymarket.com",
                polymarket_clob_base_url="https://clob.polymarket.com",
                request_timeout_seconds=15.0,
        polymarket_max_retries=2,
        polymarket_retry_backoff_seconds=0.6,
        fred_api_key=None,
        dune_api_key=None,
            ),
            "repository": FakeRepository(),
            "polymarket_client": client,
            "news_client": news_client,
        }


class FakeContext:
    def __init__(self, args: list[str], client: FakePolymarketClient, news_client: FakeNewsClient) -> None:
        self.args = args
        self.application = FakeApplication(client, news_client)


@pytest.mark.asyncio
async def test_edge_sports_filters_before_render_and_enriches_only_top_5(caplog) -> None:
    client = FakePolymarketClient()
    news_client = FakeNewsClient()
    update = FakeUpdate("/edge sports")
    context = FakeContext(["sports"], client, news_client)

    with caplog.at_level(logging.INFO):
        await edge(update, context)

    message = update.effective_message.replies[-1]
    assert "Top opportunities: sports" in message
    assert "FIFA World Cup" in message
    assert "NBA Finals" in message
    assert "Bitcoin" not in message
    assert "Fed cut" not in message
    assert "Trump" not in message
    assert client.last_limit == 250
    assert 1 <= len(news_client.calls) <= 5
    assert all(call[2] is False for call in news_client.calls)
    assert "EDGE_SCAN_START requested_category=sports" in caplog.text
    assert "EDGE_MARKETS_FILTERED requested_category=sports" in caplog.text
    assert "markets_before_filter=9" in caplog.text
    assert "markets_after_filter=6" in caplog.text
    assert "EDGE_TOP_SELECTED requested_category=sports" in caplog.text
    assert "EDGE_NEWS_ENRICHMENT_START requested_category=sports" in caplog.text
    assert "EDGE_COMPLETED requested_category=sports" in caplog.text

