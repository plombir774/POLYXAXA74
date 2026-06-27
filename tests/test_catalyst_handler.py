import sys
import types

import pytest

from app.config import Settings
from app.news.client import NewsClient
from app.polymarket.schemas import MarketData


telegram_module = types.ModuleType("telegram")
telegram_module.Update = object
telegram_ext_module = types.ModuleType("telegram.ext")
telegram_ext_module.Application = object
telegram_ext_module.CommandHandler = object
telegram_ext_module.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
sys.modules.setdefault("telegram", telegram_module)
sys.modules.setdefault("telegram.ext", telegram_ext_module)

from app.bot.handlers import catalyst


def make_market() -> MarketData:
    return MarketData(
        market_id="fed",
        slug="fed-cut-rates",
        title="Will Fed cut rates in July?",
        url="https://polymarket.com/event/fed-cut-rates",
        yes_price=0.55,
        no_price=0.45,
        volume=500_000,
        liquidity=100_000,
        spread=0.02,
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


class FakeClient:
    async def fetch_market_for_input(
        self,
        slug: str,
        input_type: str,
        enrich: bool = True,
        *,
        log_context: str | None = None,
    ) -> MarketData:
        return make_market()


class FakeApplication:
    def __init__(self) -> None:
        self.bot_data = {
            "settings": Settings(
                telegram_bot_token="token",
                telegram_allowed_user_id=123,
                openai_api_key=None,
                openai_model="gpt-5.5",
                news_provider="none",
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
            "polymarket_client": FakeClient(),
            "news_client": NewsClient(provider="none"),
        }


class FakeContext:
    args = ["fed-cut-rates"]
    application = FakeApplication()


@pytest.mark.asyncio
async def test_catalyst_command_provider_none_shows_friendly_message() -> None:
    update = FakeUpdate("/catalyst fed-cut-rates")

    await catalyst(update, FakeContext())

    message = update.effective_message.replies[-1]
    assert "Catalyst scan" in message
    assert "External news scanner is not configured. Using market data only." in message
