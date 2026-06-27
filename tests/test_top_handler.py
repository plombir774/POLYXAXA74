import sys
import types

import pytest

from app.config import Settings
from app.polymarket.schemas import MarketData


telegram_module = types.ModuleType("telegram")
telegram_module.Update = object
telegram_ext_module = types.ModuleType("telegram.ext")
telegram_ext_module.Application = object
telegram_ext_module.CommandHandler = object
telegram_ext_module.ContextTypes = types.SimpleNamespace(DEFAULT_TYPE=object)
sys.modules.setdefault("telegram", telegram_module)
sys.modules.setdefault("telegram.ext", telegram_ext_module)

from app.bot.handlers import top


def market(slug: str, title: str, volume: float) -> MarketData:
    return MarketData(
        market_id=slug,
        slug=slug,
        title=title,
        url=f"https://polymarket.com/event/{slug}",
        description="A test market.",
        yes_price=0.5,
        no_price=0.5,
        volume=volume,
        liquidity=50_000,
        spread=0.02,
        raw={},
    )


def fixture_markets() -> list[MarketData]:
    return [
        market("uzbekistan-world-cup", "Will Uzbekistan win the 2026 FIFA World Cup?", 50_000_000),
        market("usa-world-cup", "Will USA win the 2026 FIFA World Cup?", 45_000_000),
        market("bitcoin", "Will Bitcoin hit $150k by June 30, 2026?", 2_000_000),
        market("ethereum", "Will Ethereum hit $10k in 2026?", 1_000_000),
        market("fed", "Will Fed cut rates in July?", 900_000),
        market("trump", "Will Trump win the 2028 election?", 800_000),
        market("esports", "Will Global Esports win Masters London?", 700_000),
    ]


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


class FakeApplication:
    def __init__(self, client) -> None:
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
            "polymarket_client": client,
        }


class FakeContext:
    def __init__(self, args: list[str], client) -> None:
        self.args = args
        self.application = FakeApplication(client)


class FakeClient:
    def __init__(self, markets: list[MarketData]) -> None:
        self.markets = markets
        self.last_limit: int | None = None

    async def fetch_active_markets(self, limit: int = 10) -> list[MarketData]:
        self.last_limit = limit
        return self.markets


@pytest.mark.asyncio
async def test_top_handler_crypto_renders_filtered_message_not_fifa() -> None:
    client = FakeClient(fixture_markets())
    update = FakeUpdate("/top crypto")
    context = FakeContext(["crypto"], client)

    await top(update, context)

    assert client.last_limit == 200
    message = update.effective_message.replies[-1]
    assert "Bitcoin" in message
    assert "Ethereum" in message
    assert "FIFA World Cup" not in message


@pytest.mark.asyncio
async def test_top_handler_crypto_only_fifa_renders_empty_message() -> None:
    client = FakeClient(fixture_markets()[:2])
    update = FakeUpdate("/top crypto")
    context = FakeContext(["crypto"], client)

    await top(update, context)

    message = update.effective_message.replies[-1]
    assert "No active crypto markets found in the current top results." in message
    assert "FIFA World Cup" not in message
