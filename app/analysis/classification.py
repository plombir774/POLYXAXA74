from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterable

from app.polymarket.schemas import MarketData


class MarketType(StrEnum):
    NORMAL = "NORMAL"
    MEME = "MEME"
    LOTTERY = "LOTTERY"
    LOW_LIQUIDITY = "LOW_LIQUIDITY"
    EXTREME_PROBABILITY = "EXTREME_PROBABILITY"
    AMBIGUOUS_RESOLUTION = "AMBIGUOUS_RESOLUTION"
    HIGH_VOLUME_NO_EDGE = "HIGH_VOLUME_NO_EDGE"
    SHORT_DEADLINE = "SHORT_DEADLINE"
    NEWS_DRIVEN = "NEWS_DRIVEN"


@dataclass(frozen=True)
class MarketClassification:
    primary_type: MarketType
    labels: tuple[MarketType, ...]
    reasons: tuple[str, ...]

    def has(self, market_type: MarketType) -> bool:
        return market_type in self.labels

    @property
    def display(self) -> str:
        return " / ".join(label.value for label in self.labels)


MEME_TERMS = (
    "before gta vi",
    "before gta 6",
    "gta vi",
    "gta 6",
    "jesus christ return",
    "rihanna album",
    "playboi carti album",
    "alien",
    "aliens",
    "zombie",
    "celebrity",
    "joke",
    "meme coin",
    "memecoin",
    "meme",
    "go viral",
)

NEWS_TERMS = (
    "election",
    "senate",
    "president",
    "minister",
    "fed",
    "fomc",
    "inflation",
    "cpi",
    "rates",
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "sec",
    "court",
    "war",
    "ceasefire",
)

AMBIGUOUS_TERMS = (
    "significant",
    "major",
    "substantial",
    "viral",
    "controversy",
    "scandal",
    "break the internet",
    "widely considered",
    "meaningful",
    "notable",
)


def _market_text(market: MarketData) -> str:
    raw_values = []
    for key in (
        "category",
        "subcategory",
        "eventSlug",
        "seriesSlug",
        "tags",
        "tag",
        "categories",
        "sector",
    ):
        value = market.raw.get(key)
        if value:
            raw_values.append(str(value))
    for event in market.raw.get("events") or []:
        if isinstance(event, dict):
            raw_values.extend(str(event.get(key, "")) for key in ("slug", "title", "category"))
    return " ".join(
        [
            market.title or "",
            market.slug or "",
            market.description or "",
            " ".join(raw_values),
        ]
    ).lower()


def _days_until(market: MarketData) -> float | None:
    if market.end_date is None:
        return None
    end = market.end_date if market.end_date.tzinfo else market.end_date.replace(tzinfo=UTC)
    return (end - datetime.now(UTC)).total_seconds() / 86400


def is_news_context(market: MarketData) -> bool:
    text = _market_text(market)
    return any(term in text for term in NEWS_TERMS)


def classify_market(
    market: MarketData,
    *,
    signal_score: int | None = None,
    spread_score: int | None = None,
) -> MarketClassification:
    text = _market_text(market)
    labels: list[MarketType] = []
    reasons: list[str] = []

    # V2.3: markets with deep volume AND liquidity are treated as serious even
    # if their wording resembles a meme market (e.g. "Will X happen before GTA VI?"
    # at $11M+ volume with 1% spread is a serious proxy bet on GTA VI release date).
    high_volume_serious = (market.volume or 0) >= 5_000_000 and (
        market.liquidity is not None and market.liquidity >= 100_000
    )

    meme_match = any(term in text for term in MEME_TERMS)
    if meme_match and not is_news_context(market):
        if high_volume_serious:
            reasons.append("Meme-style wording detected, but deep volume and liquidity suggest a serious proxy bet rather than a joke market.")
        else:
            labels.append(MarketType.MEME)
            reasons.append("Meme-style wording can attract volume without durable information edge.")
    elif meme_match:
        if high_volume_serious:
            reasons.append("Meme-style wording is present, but deep volume and liquidity suggest a serious proxy bet.")
        else:
            labels.append(MarketType.MEME)
            reasons.append("Meme-style wording is present even though the market also has news context.")

    if market.yes_price is not None:
        # V2.3: lottery label only applies to genuinely thin / low-volume tail bets.
        # A low-priced market with deep liquidity and high volume is a legitimate
        # tail-risk contract (e.g. BTC $150k at 0.4% with $20M volume) — NOT a lottery ticket.
        lottery_eligible = not high_volume_serious
        if market.yes_price < 0.03 or market.yes_price > 0.97:
            labels.append(MarketType.EXTREME_PROBABILITY)
            if lottery_eligible:
                labels.append(MarketType.LOTTERY)
                reasons.append("YES price is below 3% or above 97%, so payoff is lottery-style.")
            else:
                reasons.append("YES price is below 3% or above 97%, but deep liquidity and volume make this a serious tail-risk contract rather than a lottery ticket.")
        elif market.yes_price < 0.08 or market.yes_price > 0.92:
            labels.append(MarketType.EXTREME_PROBABILITY)
            reasons.append("YES price is very close to 0% or 100%.")

    if market.liquidity is None or market.liquidity < 5_000:
        labels.append(MarketType.LOW_LIQUIDITY)
        reasons.append("Low liquidity can make prices noisy and hard to interpret.")

    if any(term in text for term in AMBIGUOUS_TERMS):
        labels.append(MarketType.AMBIGUOUS_RESOLUTION)
        reasons.append("Resolution language looks subjective or vague.")

    days = _days_until(market)
    if days is not None and 0 <= days < 1:
        labels.append(MarketType.SHORT_DEADLINE)
        reasons.append("Market is close to resolution, so stale or binary event risk is higher.")

    if is_news_context(market):
        labels.append(MarketType.NEWS_DRIVEN)
        reasons.append("Market appears tied to news, macro, politics, crypto, or sports events.")

    high_volume = (market.volume or 0) >= 1_000_000
    tight_spread = market.spread is not None and market.spread <= 0.02
    weak_score = signal_score is not None and signal_score < 65
    meme_no_edge = MarketType.MEME in labels and high_volume and tight_spread
    if high_volume and tight_spread and (weak_score or meme_no_edge):
        labels.append(MarketType.HIGH_VOLUME_NO_EDGE)
        reasons.append("High volume and tight spread are present, but deterministic signal is weak.")

    labels = _dedupe(labels)
    if not labels:
        labels = [MarketType.NORMAL]
        reasons = ["No special market-quality warning detected."]

    primary = _choose_primary(labels)
    return MarketClassification(primary_type=primary, labels=tuple(labels), reasons=tuple(reasons))


def _dedupe(labels: Iterable[MarketType]) -> list[MarketType]:
    seen: set[MarketType] = set()
    result = []
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        result.append(label)
    return result


def _choose_primary(labels: list[MarketType]) -> MarketType:
    priority = (
        MarketType.LOTTERY,
        MarketType.MEME,
        MarketType.LOW_LIQUIDITY,
        MarketType.AMBIGUOUS_RESOLUTION,
        MarketType.EXTREME_PROBABILITY,
        MarketType.HIGH_VOLUME_NO_EDGE,
        MarketType.SHORT_DEADLINE,
        MarketType.NEWS_DRIVEN,
        MarketType.NORMAL,
    )
    for label in priority:
        if label in labels:
            return label
    return labels[0]


def market_type_notes(classification: MarketClassification) -> list[str]:
    return list(classification.reasons)


def is_obvious_meme_market(market: MarketData) -> bool:
    return classify_market(market).has(MarketType.MEME)
