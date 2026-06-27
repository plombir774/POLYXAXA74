from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def sqlite_path_from_url(database_url: str) -> str:
    if database_url == "sqlite:///:memory:":
        return ":memory:"
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        if raw_path == ":memory:":
            return ":memory:"
        return str(Path(raw_path).expanduser())
    if database_url.startswith("sqlite://"):
        raw_path = database_url.removeprefix("sqlite://")
        return str(Path(raw_path).expanduser())
    raise ValueError("Only sqlite:/// DATABASE_URL values are supported")


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_allowed_user_id: int
    openai_api_key: str | None
    openai_model: str
    news_provider: str
    news_api_key: str | None
    news_lookback_hours: int
    news_max_results: int
    database_url: str
    check_interval_minutes: int
    min_signal_score: int
    meme_allow_volume_threshold: int
    polymarket_gamma_base_url: str
    polymarket_clob_base_url: str
    request_timeout_seconds: float
    polymarket_max_retries: int
    polymarket_retry_backoff_seconds: float
    fred_api_key: str | None
    dune_api_key: str | None

    @property
    def database_path(self) -> str:
        return sqlite_path_from_url(self.database_url)


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_allowed_user_id=_get_int("TELEGRAM_ALLOWED_USER_ID", 0),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.5"),
        news_provider=os.getenv("NEWS_PROVIDER", "newsapi").strip().lower() or "newsapi",
        news_api_key=os.getenv("NEWS_API_KEY") or None,
        news_lookback_hours=_get_int("NEWS_LOOKBACK_HOURS", 24),
        news_max_results=_get_int("NEWS_MAX_RESULTS", 5),
        database_url=os.getenv("DATABASE_URL", "sqlite:///polymarket_bot.db"),
        check_interval_minutes=_get_int("CHECK_INTERVAL_MINUTES", 60),
        min_signal_score=_get_int("MIN_SIGNAL_SCORE", 70),
        meme_allow_volume_threshold=_get_int("MEME_ALLOW_VOLUME_THRESHOLD", 10_000_000),
        polymarket_gamma_base_url=os.getenv(
            "POLYMARKET_GAMMA_BASE_URL", "https://gamma-api.polymarket.com"
        ),
        polymarket_clob_base_url=os.getenv(
            "POLYMARKET_CLOB_BASE_URL", "https://clob.polymarket.com"
        ),
        request_timeout_seconds=_get_float("REQUEST_TIMEOUT_SECONDS", 15.0),
        polymarket_max_retries=_get_int("POLYMARKET_MAX_RETRIES", 2),
        polymarket_retry_backoff_seconds=_get_float("POLYMARKET_RETRY_BACKOFF_SECONDS", 0.6),
        fred_api_key=os.getenv("FRED_API_KEY") or None,
        dune_api_key=os.getenv("DUNE_API_KEY") or None,
    )


def validate_runtime_settings(settings: Settings) -> None:
    missing = []
    if not settings.telegram_bot_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not settings.telegram_allowed_user_id:
        missing.append("TELEGRAM_ALLOWED_USER_ID")
    if missing:
        names = ", ".join(missing)
        raise RuntimeError(f"Missing required environment variable(s): {names}")
