from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx

from app.news.models import NewsItem, NewsScanResult
from app.news.providers import NewsProvider, RSSProvider
from app.news.sentiment import analyze_news_sentiment, calculate_sentiment_confidence


logger = logging.getLogger(__name__)


def build_market_query(market_title: str) -> str:
    title = _clean_title(market_title)
    text = title.lower()
    if any(term in text for term in ("bitcoin", "btc")):
        return "Bitcoin BTC crypto market"
    if any(term in text for term in ("ethereum", "eth")):
        return "Ethereum ETH crypto market"
    if "china" in text and "taiwan" in text:
        return "China Taiwan geopolitical news"
    if "trump" in text and ("president" in text or "presidency" in text):
        return "Trump presidency politics"
    if "trump" in text or "election" in text:
        return f"{_key_terms(title)} politics election"
    if any(term in text for term in ("fed", "fomc", "cpi", "rates", "inflation", "gdp")):
        return f"{_key_terms(title)} macro economy"
    if any(term in text for term in ("oil", "gold", "recession")):
        return f"{_key_terms(title)} macro market"
    if any(term in text for term in ("fifa", "world cup", "nba", "nfl", "soccer", "football", "ufc")):
        return f"{_key_terms(title)} sports odds injury lineup"
    return f"{_key_terms(title)} news"


async def scan_market_news(
    market_title: str,
    market_type: str,
    lookback_hours: int = 24,
    limit: int = 5,
    provider: NewsProvider | None = None,
    openai_api_key: str | None = None,
    openai_model: str = "gpt-5.5",
    timeout_seconds: float = 15.0,
) -> NewsScanResult:
    query = build_market_query(market_title)
    provider = provider or RSSProvider(timeout_seconds=timeout_seconds)
    provider_name = getattr(provider, "name", provider.__class__.__name__.lower())
    logger.info(
        "news_scan_started market_title=%r market_type=%s provider=%s lookback_hours=%s limit=%s",
        market_title,
        market_type,
        provider_name,
        lookback_hours,
        limit,
    )
    logger.info("news_provider_used provider=%s", provider_name)
    try:
        raw_items = await provider.search(query, limit=max(limit * 3, limit), lookback_hours=lookback_hours)
    except Exception as exc:
        logger.warning(
            "news_scan_provider_failed provider=%s error_class=%s message=%s",
            provider_name,
            exc.__class__.__name__,
            _short_message(exc),
        )
        return NewsScanResult(
            news_found=False,
            provider=provider_name,
            query=query,
            error=_short_message(exc),
        )

    items = deduplicate_news_items(raw_items)
    scored = [
        (item, calculate_relevance(market_title, item.title, item.summary))
        for item in items
    ]
    scored = [(item, score) for item, score in scored if score >= 45]
    scored.sort(key=lambda entry: (entry[1], entry[0].published_at), reverse=True)
    selected_items = [item for item, _score in scored[:limit]]
    relevance_scores = [score for _item, score in scored[:limit]]
    sentiment = analyze_news_sentiment(selected_items)
    sentiment_confidence = calculate_sentiment_confidence(selected_items, sentiment)
    catalyst_score = calculate_catalyst_score(
        selected_items,
        relevance_scores,
        sentiment_confidence=sentiment_confidence,
    )
    summary = _build_summary(selected_items, catalyst_score)
    if openai_api_key and selected_items:
        try:
            summary = await _try_openai_summary(
                market_title=market_title,
                items=selected_items,
                fallback=summary,
                api_key=openai_api_key,
                model=openai_model,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            logger.warning(
                "news_openai_summary_failed error_class=%s message=%s",
                exc.__class__.__name__,
                _short_message(exc),
            )
    result = NewsScanResult(
        news_found=bool(selected_items),
        items=selected_items,
        sentiment=sentiment,
        confidence=sentiment_confidence,
        catalyst_score=catalyst_score,
        summary=summary,
        provider=provider_name,
        query=query,
    )
    logger.info(
        "news_scan_completed provider=%s news_items_found=%s news_sentiment=%s catalyst_score=%s",
        provider_name,
        len(selected_items),
        sentiment,
        catalyst_score,
    )
    logger.info("news_items_found count=%s", len(selected_items))
    logger.info("news_sentiment sentiment=%s confidence=%s", sentiment, sentiment_confidence)
    logger.info("catalyst_score score=%s", catalyst_score)
    return result


def deduplicate_news_items(items: Sequence[NewsItem]) -> list[NewsItem]:
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    deduped = []
    for item in items:
        title_key = _normalize_key(item.title)
        url_key = item.url.strip().lower()
        if title_key and title_key in seen_titles:
            continue
        if url_key and url_key in seen_urls:
            continue
        if title_key:
            seen_titles.add(title_key)
        if url_key:
            seen_urls.add(url_key)
        deduped.append(item)
    return deduped


def calculate_relevance(market_title: str, news_title: str, news_summary: str) -> int:
    market_tokens = _weighted_tokens(market_title)
    news_tokens = _weighted_tokens(f"{news_title} {news_summary}")
    if not market_tokens or not news_tokens:
        return 0
    news_token_set = set(news_tokens)
    overlap = set(market_tokens).intersection(news_token_set)
    entity_score = catalyst_entity_relevance(market_title, f"{news_title} {news_summary}")
    if entity_score == 0 and _requires_entity_match(market_title):
        return min(20, len(overlap) * 8)
    if not overlap:
        return entity_score
    base = int(100 * len(overlap) / max(3, len(set(market_tokens))))
    important_bonus = sum(30 for token in overlap if token in _important_entities(market_title))
    return max(0, min(100, base + important_bonus + entity_score))


def catalyst_entity_relevance(market_title: str, news_text: str) -> int:
    market_entities = _market_entities(market_title)
    if not market_entities:
        return 25
    news_tokens = set(_weighted_tokens(news_text))
    score = 0
    for entity in market_entities:
        aliases = ENTITY_ALIASES.get(entity, {entity})
        if aliases.intersection(news_tokens):
            score += 50
    return min(70, score)


def calculate_catalyst_score(
    items: Sequence[NewsItem],
    relevance_scores: Sequence[int] | None = None,
    *,
    sentiment_confidence: int = 0,
) -> int:
    if not items:
        return 0
    scores = list(relevance_scores or [40] * len(items))
    average_relevance = sum(scores) / len(scores) if scores else 0
    article_factor = min(25, len(items) * 8)
    recency_factor = _recency_factor(items)
    sentiment_factor = min(20, sentiment_confidence * 0.2)
    score = average_relevance * 0.4 + article_factor + recency_factor + sentiment_factor
    return max(0, min(100, round(score)))


def catalyst_strength_label(score: int) -> str:
    if score <= 20:
        return "weak"
    if score <= 50:
        return "moderate"
    if score <= 75:
        return "strong"
    return "major"


async def _try_openai_summary(
    *,
    market_title: str,
    items: Sequence[NewsItem],
    fallback: str,
    api_key: str,
    model: str,
    timeout_seconds: float,
) -> str:
    body = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Summarize recent external news catalysts for a private "
                            "analysis-only prediction-market bot. Do not give financial advice, "
                            "betting instructions, or guaranteed profit claims."
                        ),
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": json.dumps(
                            {
                                "market_title": market_title,
                                "articles": [
                                    {
                                        "title": item.title,
                                        "source": item.source,
                                        "summary": item.summary,
                                    }
                                    for item in items[:5]
                                ],
                            },
                            default=str,
                        ),
                    }
                ],
            },
        ],
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        logger.warning(
            "news_openai_summary_failed error_class=%s message=%s",
            exc.__class__.__name__,
            _short_message(exc),
        )
        return fallback
    text = _extract_output_text(payload)
    return text if text else fallback


def _build_summary(items: Sequence[NewsItem], catalyst_score: int) -> str:
    if not items:
        return "No relevant external catalysts found during the selected lookback window."
    top_titles = "; ".join(item.title for item in items[:3])
    strength = catalyst_strength_label(catalyst_score)
    return f"{strength.capitalize()} catalyst backdrop from recent coverage: {top_titles}."


def _recency_factor(items: Sequence[NewsItem]) -> int:
    now = datetime.now(UTC)
    values = []
    for item in items:
        published = item.published_at if item.published_at.tzinfo else item.published_at.replace(tzinfo=UTC)
        age_hours = max(0, (now - published).total_seconds() / 3600)
        if age_hours <= 6:
            values.append(25)
        elif age_hours <= 24:
            values.append(18)
        elif age_hours <= 72:
            values.append(8)
        else:
            values.append(0)
    return round(sum(values) / len(values)) if values else 0


def _clean_title(value: str) -> str:
    text = value.replace("?", "")
    return re.sub(r"\s+", " ", text).strip()


def _key_terms(value: str) -> str:
    tokens = _weighted_tokens(value)
    return " ".join(tokens[:5]) if tokens else _clean_title(value)


def _weighted_tokens(value: str) -> list[str]:
    stop = {
        "will",
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "market",
        "before",
        "after",
        "into",
        "from",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
        "2024",
        "2025",
        "2026",
        "2027",
        "2028",
        "world",
        "cup",
        "fifa",
        "win",
        "wins",
        "odds",
        "market",
    }
    tokens = [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) >= 3]
    return list(dict.fromkeys(token for token in tokens if token not in stop))


def _important_entities(value: str) -> set[str]:
    text = value.lower()
    entities = set()
    for term in (
        "bitcoin",
        "btc",
        "ethereum",
        "eth",
        "china",
        "taiwan",
        "trump",
        "fed",
        "cpi",
        "inflation",
        "sec",
        "fifa",
    ):
        if term in text:
            entities.add(term)
    return entities


COUNTRY_TERMS = {
    "argentina",
    "brazil",
    "china",
    "england",
    "france",
    "germany",
    "norway",
    "portugal",
    "spain",
    "taiwan",
    "usa",
    "united",
    "states",
}

POLITICIAN_TERMS = {
    "trump",
    "biden",
    "putin",
    "zelensky",
    "macron",
    "modi",
}

TEAM_PERSON_TERMS = {
    "haaland",
    "mbappe",
    "messi",
    "ronaldo",
    "neymar",
}

ENTITY_ALIASES = {
    "norway": {"norway", "norwegian", "haaland", "odegaard"},
    "france": {"france", "french", "mbappe"},
    "argentina": {"argentina", "argentine", "messi"},
    "portugal": {"portugal", "portuguese", "ronaldo"},
    "brazil": {"brazil", "brazilian", "neymar"},
    "spain": {"spain", "spanish"},
    "usa": {"usa", "united", "states", "american"},
    "trump": {"trump"},
    "biden": {"biden"},
    "china": {"china", "chinese"},
    "taiwan": {"taiwan", "taiwanese"},
}


def _market_entities(market_title: str) -> set[str]:
    tokens = set(_weighted_tokens(market_title))
    entities = tokens.intersection(COUNTRY_TERMS | POLITICIAN_TERMS | TEAM_PERSON_TERMS)
    if {"united", "states"}.issubset(tokens):
        entities.add("usa")
    return entities


def _requires_entity_match(market_title: str) -> bool:
    return bool(_market_entities(market_title))


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _extract_output_text(response: dict) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str):
        return direct.strip()
    chunks = []
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                chunks.append(content["text"])
    return "\n".join(chunks).strip()


def _short_message(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ").strip()
    return message[:240] if message else exc.__class__.__name__
