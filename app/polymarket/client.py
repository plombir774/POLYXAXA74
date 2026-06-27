from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.polymarket.schemas import MarketData


logger = logging.getLogger(__name__)


class PolymarketAPIError(RuntimeError):
    """Raised when a Polymarket public API request fails."""


class MarketLookupError(RuntimeError):
    """Raised when a market lookup completes but no usable market is selected."""


class MarketNotFoundError(MarketLookupError):
    """Raised when no active market matches a slug or URL."""


class EventHasNoActiveMarketsError(MarketLookupError):
    """Raised when an event exists but has no active markets."""


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_json_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _first_present(raw: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def _prices_from_outcomes(raw: dict[str, Any]) -> tuple[float | None, float | None]:
    outcomes = [str(item).strip().lower() for item in _coerce_json_list(raw.get("outcomes"))]
    prices = [_coerce_float(item) for item in _coerce_json_list(raw.get("outcomePrices"))]
    if not prices:
        return None, None

    yes_price = None
    no_price = None
    if outcomes:
        for index, outcome in enumerate(outcomes):
            if index >= len(prices):
                continue
            if outcome in {"yes", "y"}:
                yes_price = prices[index]
            elif outcome in {"no", "n"}:
                no_price = prices[index]
    if yes_price is None and len(prices) >= 1:
        yes_price = prices[0]
    if no_price is None and len(prices) >= 2:
        no_price = prices[1]
    if no_price is None and yes_price is not None and 0 <= yes_price <= 1:
        no_price = 1 - yes_price
    return yes_price, no_price


def _first_token_id(raw: dict[str, Any]) -> str | None:
    token_ids = _coerce_json_list(raw.get("clobTokenIds"))
    if not token_ids:
        token_ids = _coerce_json_list(raw.get("clob_token_ids"))
    if token_ids:
        return str(token_ids[0])
    token_id = raw.get("token_id") or raw.get("tokenId")
    return str(token_id) if token_id else None


def _slugify(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def _extract_url_slug(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    parsed = urlparse(text)
    path = parsed.path if parsed.scheme else text
    parts = [part for part in path.split("/") if part]
    if not parts:
        return None
    return parts[-1].split("?", 1)[0].split("#", 1)[0]


def _is_resolved(raw: dict[str, Any]) -> bool:
    status_fields = (
        "umaResolutionStatus",
        "umaResolutionStatuses",
        "resolutionStatus",
        "resolvedBy",
        "closedTime",
    )
    for field in status_fields:
        value = raw.get(field)
        if value in (None, ""):
            continue
        normalized = str(value).lower()
        tokens = set(re.split(r"[^a-z]+", normalized))
        if "unresolved" in tokens:
            continue
        if tokens.intersection({"resolved", "settled", "finalized", "closed"}):
            return True
    return False


def is_active_open_market(raw: dict[str, Any]) -> bool:
    if bool(raw.get("closed", False)) or bool(raw.get("archived", False)):
        return False
    if raw.get("active") is False:
        return False
    if raw.get("acceptingOrders") is False:
        return False
    return not _is_resolved(raw)


def market_sort_volume(raw: dict[str, Any]) -> tuple[float, float, float]:
    volume = _coerce_float(_first_present(raw, ("volumeNum", "volume", "volumeClob", "volumeAmm"))) or 0
    volume_24hr = (
        _coerce_float(_first_present(raw, ("volume24hr", "volume24hrClob", "volume24hrAmm")))
        or 0
    )
    liquidity = (
        _coerce_float(_first_present(raw, ("liquidityNum", "liquidity", "liquidityClob", "liquidityAmm")))
        or 0
    )
    return volume, volume_24hr, liquidity


def select_highest_volume_active_market(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    active = [candidate for candidate in candidates if is_active_open_market(candidate)]
    if not active:
        return None
    return max(active, key=market_sort_volume)


def _event_slugs(raw: dict[str, Any]) -> set[str]:
    slugs: set[str] = set()
    for event in _coerce_json_list(raw.get("events")):
        if isinstance(event, dict):
            slug = event.get("slug")
            if slug:
                slugs.add(str(slug).lower())
    for key in ("eventSlug", "seriesSlug"):
        value = raw.get(key)
        if value:
            slugs.add(str(value).lower())
    return slugs


def market_matches_slug(raw: dict[str, Any], slug: str) -> bool:
    query = slug.lower()
    query_slug = _slugify(slug)
    exact_fields = (
        "slug",
        "marketSlug",
        "urlSlug",
        "conditionSlug",
    )
    for field in exact_fields:
        value = raw.get(field)
        if value and str(value).lower() == query:
            return True

    for field in ("url", "marketUrl", "eventUrl"):
        url_slug = _extract_url_slug(raw.get(field))
        if url_slug and url_slug.lower() == query:
            return True

    if query in _event_slugs(raw):
        return True

    text_fields = (
        raw.get("question"),
        raw.get("title"),
        raw.get("name"),
        raw.get("groupItemTitle"),
    )
    for value in text_fields:
        text_slug = _slugify(value)
        if text_slug and (text_slug == query_slug or query_slug in text_slug):
            return True
    return False


# ---------- Free-text fuzzy matching helpers (V2.3) ----------

_TEXT_STOP_WORDS = frozenset({
    "a", "an", "the", "will", "won't", "is", "are", "be", "by", "for",
    "of", "to", "in", "on", "at", "or", "and", "if", "it", "this", "that",
    "these", "those", "as", "with", "from", "into", "than", "then", "so",
    "do", "does", "did", "has", "have", "had", "can", "could", "would",
    "should", "may", "might", "shall", "must",
})


def _normalize_query_text(value: str) -> str:
    lowered = value.lower().strip()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def _meaningful_tokens(text: str) -> list[str]:
    return [t for t in text.split() if t and t not in _TEXT_STOP_WORDS]


def _text_similarity(query: str, candidate: str) -> float:
    if not query or not candidate:
        return 0.0
    q_tokens = _meaningful_tokens(query)
    c_tokens = set(_meaningful_tokens(candidate))
    if not q_tokens:
        return 0.0
    matches = sum(1 for t in q_tokens if t in c_tokens)
    return matches / len(q_tokens)


def _candidate_question_text(raw: dict[str, Any]) -> str:
    for key in ("question", "title", "name", "groupItemTitle"):
        value = raw.get(key)
        if value:
            return _normalize_query_text(str(value))
    return ""


def rank_markets_by_text_query(
    candidates: list[dict[str, Any]],
    query: str,
) -> list[tuple[float, dict[str, Any]]]:
    """Rank active markets by fuzzy similarity to a free-text query."""
    normalized_query = _normalize_query_text(query)
    if not normalized_query:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for raw in candidates:
        if not isinstance(raw, dict) or not is_active_open_market(raw):
            continue
        candidate = _candidate_question_text(raw)
        if not candidate:
            continue
        if normalized_query == candidate:
            score = 1.0
        elif normalized_query in candidate or candidate in normalized_query:
            score = 0.9
        else:
            score = _text_similarity(normalized_query, candidate)
        if score >= 0.5:
            scored.append((score, raw))
    scored.sort(key=lambda item: (item[0], market_sort_volume(item[1])), reverse=True)
    return scored


class PolymarketClient:
    def __init__(
        self,
        gamma_base_url: str = "https://gamma-api.polymarket.com",
        clob_base_url: str = "https://clob.polymarket.com",
        timeout_seconds: float = 15.0,
        *,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.6,
    ) -> None:
        self.gamma_base_url = gamma_base_url.rstrip("/")
        self.clob_base_url = clob_base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff_seconds = max(0.0, float(retry_backoff_seconds))

    def _log_endpoint(
        self,
        *,
        log_context: str | None,
        strategy: str,
        endpoint: str,
        status_code: int | None,
        parsed_slug: str | None = None,
    ) -> None:
        if log_context != "forecast":
            return
        logger.info(
            "forecast_lookup_endpoint strategy=%s endpoint=%s status_code=%s parsed_slug=%s",
            strategy,
            endpoint,
            status_code,
            parsed_slug,
        )

    async def _backoff(self, attempt: int) -> None:
        import asyncio
        delay = self.retry_backoff_seconds * (2 ** attempt)
        await asyncio.sleep(delay)

    async def _request_json(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        strategy: str = "request",
        parsed_slug: str | None = None,
        log_context: str | None = None,
    ) -> tuple[int, Any]:
        url = f"{base_url}/{path.lstrip('/')}"
        status_code: int | None = None
        last_exc: Exception | None = None
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(url, params=params)
                    status_code = response.status_code
                    self._log_endpoint(
                        log_context=log_context,
                        strategy=strategy,
                        endpoint=str(response.url),
                        status_code=status_code,
                        parsed_slug=parsed_slug,
                    )
                    # 4xx — do not retry (client error). 5xx — retry.
                    if 400 <= status_code < 500:
                        return status_code, None
                    if status_code >= 500 and attempt < attempts - 1:
                        await self._backoff(attempt)
                        continue
                    if status_code >= 400:
                        return status_code, None
                    return status_code, response.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last_exc = exc
                if attempt < attempts - 1:
                    logger.warning(
                        "polymarket_api_retry attempt=%s/%s url=%s error=%s",
                        attempt + 1, attempts, url, exc.__class__.__name__,
                    )
                    await self._backoff(attempt)
                    continue
                self._log_endpoint(
                    log_context=log_context,
                    strategy=strategy,
                    endpoint=url,
                    status_code=status_code,
                    parsed_slug=parsed_slug,
                )
                raise PolymarketAPIError(f"Polymarket API request failed for {url}") from exc
        if last_exc is not None:
            raise PolymarketAPIError(f"Polymarket API request failed for {url}") from last_exc
        raise PolymarketAPIError(f"Polymarket API request failed for {url}")

    async def _get_json(
        self,
        base_url: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{base_url}/{path.lstrip('/')}"
        status_code, data = await self._request_json(base_url, path, params)
        if status_code >= 400:
            raise PolymarketAPIError(f"Polymarket API returned {status_code} for {url}")
        return data

    async def fetch_active_markets(self, limit: int = 10) -> list[MarketData]:
        data = await self._get_json(
            self.gamma_base_url,
            "/markets",
            {
                "active": "true",
                "closed": "false",
                "order": "volume_24hr",
                "ascending": "false",
                "limit": limit,
            },
        )
        if not isinstance(data, list):
            raise PolymarketAPIError("Unexpected active markets response")
        return [self.parse_market(item) for item in data if isinstance(item, dict)]

    async def _fetch_direct_market(
        self,
        slug: str,
        *,
        log_context: str | None = None,
    ) -> dict[str, Any] | None:
        status_code, raw = await self._request_json(
            self.gamma_base_url,
            f"/markets/slug/{slug}",
            strategy="direct_market_slug",
            parsed_slug=slug,
            log_context=log_context,
        )
        if status_code == 404:
            return None
        if status_code >= 400:
            raise PolymarketAPIError(
                f"Polymarket API returned {status_code} for /markets/slug/{slug}"
            )
        return raw if isinstance(raw, dict) else None

    async def _fetch_active_market_candidates(
        self,
        slug: str,
        *,
        limit: int = 500,
        log_context: str | None = None,
    ) -> list[dict[str, Any]]:
        status_code, data = await self._request_json(
            self.gamma_base_url,
            "/markets",
            {
                "active": "true",
                "closed": "false",
                "order": "volume_24hr",
                "ascending": "false",
                "limit": limit,
            },
            strategy="active_markets_search",
            parsed_slug=slug,
            log_context=log_context,
        )
        if status_code >= 400:
            raise PolymarketAPIError(f"Polymarket API returned {status_code} for /markets")
        if not isinstance(data, list):
            raise PolymarketAPIError("Unexpected active markets response")
        matches = [
            item
            for item in data
            if isinstance(item, dict)
            and is_active_open_market(item)
            and market_matches_slug(item, slug)
        ]
        if log_context == "forecast":
            logger.info(
                "forecast_lookup_strategy strategy=active_markets_search parsed_slug=%s matches=%s",
                slug,
                len(matches),
            )
        return matches

    async def _fetch_event_by_slug(
        self,
        slug: str,
        *,
        log_context: str | None = None,
    ) -> dict[str, Any] | None:
        status_code, data = await self._request_json(
            self.gamma_base_url,
            f"/events/slug/{slug}",
            strategy="event_slug",
            parsed_slug=slug,
            log_context=log_context,
        )
        if status_code == 404:
            status_code, data = await self._request_json(
                self.gamma_base_url,
                "/events",
                {"slug": slug, "active": "true", "closed": "false", "limit": 1},
                strategy="event_list_slug",
                parsed_slug=slug,
                log_context=log_context,
            )
            if status_code == 404:
                return None
            if status_code >= 400:
                raise PolymarketAPIError(f"Polymarket API returned {status_code} for /events")
            if isinstance(data, list) and data:
                first = data[0]
                return first if isinstance(first, dict) else None
            return None
        if status_code >= 400:
            raise PolymarketAPIError(f"Polymarket API returned {status_code} for /events/slug/{slug}")
        return data if isinstance(data, dict) else None

    def _log_selected_market(
        self,
        *,
        log_context: str | None,
        strategy: str,
        slug: str,
        market: MarketData,
    ) -> None:
        if log_context != "forecast":
            return
        logger.info(
            "forecast_lookup_selected strategy=%s parsed_slug=%s selected_market_id=%s selected_question=%r",
            strategy,
            slug,
            market.market_id,
            market.title,
        )

    async def fetch_market_by_slug(
        self,
        slug: str,
        enrich: bool = True,
        *,
        input_type: str = "slug",
        log_context: str | None = None,
    ) -> MarketData:
        raw = await self._fetch_direct_market(slug, log_context=log_context)
        if raw and is_active_open_market(raw):
            market = self.parse_market(raw)
            if enrich:
                market = await self.enrich_with_clob_data(market)
            self._log_selected_market(
                log_context=log_context,
                strategy="direct_market_slug",
                slug=slug,
                market=market,
            )
            return market

        candidates = await self._fetch_active_market_candidates(slug, log_context=log_context)
        selected = select_highest_volume_active_market(candidates)
        if selected:
            market = self.parse_market(selected)
            if enrich:
                market = await self.enrich_with_clob_data(market)
            self._log_selected_market(
                log_context=log_context,
                strategy="active_markets_search",
                slug=slug,
                market=market,
            )
            return market

        event = await self._fetch_event_by_slug(slug, log_context=log_context)
        if event is not None:
            event_markets = [
                item for item in _coerce_json_list(event.get("markets")) if isinstance(item, dict)
            ]
            selected = select_highest_volume_active_market(event_markets)
            if selected is None:
                raise EventHasNoActiveMarketsError(slug)
            market = self.parse_market(selected)
            if enrich:
                market = await self.enrich_with_clob_data(market)
            self._log_selected_market(
                log_context=log_context,
                strategy="event_markets",
                slug=slug,
                market=market,
            )
            return market

        if log_context == "forecast":
            logger.info(
                "forecast_lookup_no_match parsed_slug=%s input_type=%s",
                slug,
                input_type,
            )
        raise MarketNotFoundError(slug)

    async def fetch_market_for_input(
        self,
        slug: str,
        input_type: str,
        enrich: bool = True,
        *,
        log_context: str | None = None,
    ) -> MarketData:
        return await self.fetch_market_by_slug(
            slug,
            enrich=enrich,
            input_type=input_type,
            log_context=log_context,
        )

    async def fetch_market_by_text_query(
        self,
        query: str,
        enrich: bool = True,
        *,
        log_context: str | None = None,
        candidate_limit: int = 500,
    ) -> MarketData:
        """Find a market by a free-text query (the market's own question/title).

        Used as a fallback when the user pastes the full question text instead
        of a slug or URL. Ranks active markets by token similarity.
        """
        normalized = _normalize_query_text(query)
        if not normalized:
            raise MarketNotFoundError(query)
        if log_context == "forecast":
            logger.info(
                "forecast_lookup_strategy strategy=text_query query=%r normalized=%r",
                query, normalized,
            )
        status_code, data = await self._request_json(
            self.gamma_base_url,
            "/markets",
            {
                "active": "true",
                "closed": "false",
                "order": "volume_24hr",
                "ascending": "false",
                "limit": candidate_limit,
            },
            strategy="text_query_search",
            parsed_slug=normalized,
            log_context=log_context,
        )
        if status_code >= 400:
            raise PolymarketAPIError(f"Polymarket API returned {status_code} for /markets (text query)")
        if not isinstance(data, list):
            raise PolymarketAPIError("Unexpected active markets response")
        ranked = rank_markets_by_text_query(data, query)
        if not ranked:
            if log_context == "forecast":
                logger.info("forecast_lookup_no_match strategy=text_query query=%r", query)
            raise MarketNotFoundError(query)
        score, selected = ranked[0]
        market = self.parse_market(selected)
        if enrich:
            market = await self.enrich_with_clob_data(market)
        self._log_selected_market(
            log_context=log_context,
            strategy=f"text_query_search (score={score:.2f})",
            slug=normalized,
            market=market,
        )
        return market

    async def fetch_midpoint(self, token_id: str) -> float | None:
        try:
            data = await self._get_json(
                self.clob_base_url, "/midpoint", {"token_id": token_id}
            )
        except PolymarketAPIError:
            return None
        if isinstance(data, dict):
            return _coerce_float(data.get("mid_price"))
        return None

    async def fetch_spread(self, token_id: str) -> float | None:
        try:
            data = await self._get_json(self.clob_base_url, "/spread", {"token_id": token_id})
        except PolymarketAPIError:
            return None
        if isinstance(data, dict):
            return _coerce_float(data.get("spread"))
        return None

    async def enrich_with_clob_data(self, market: MarketData) -> MarketData:
        token_id = _first_token_id(market.raw)
        if not token_id:
            return market
        yes_price = market.yes_price
        spread = market.spread
        import asyncio
        tasks: list[Any] = []
        if yes_price is None:
            tasks.append(("midpoint", self.fetch_midpoint(token_id)))
        if spread is None:
            tasks.append(("spread", self.fetch_spread(token_id)))
        if tasks:
            results = await asyncio.gather(
                *(task for _, task in tasks), return_exceptions=True
            )
            for (name, _), result in zip(tasks, results):
                if isinstance(result, Exception):
                    continue
                if name == "midpoint":
                    yes_price = result
                elif name == "spread":
                    spread = result
        no_price = market.no_price
        if no_price is None and yes_price is not None and 0 <= yes_price <= 1:
            no_price = 1 - yes_price
        return market.model_copy(update={"yes_price": yes_price, "no_price": no_price, "spread": spread})

    def parse_market(self, raw: dict[str, Any]) -> MarketData:
        yes_price, no_price = _prices_from_outcomes(raw)
        slug = str(_first_present(raw, ("slug", "marketSlug")) or "")
        title = str(_first_present(raw, ("question", "title", "name")) or slug or "Untitled market")
        market_id = str(_first_present(raw, ("id", "conditionId", "questionID", "slug")) or slug)
        volume = _coerce_float(_first_present(raw, ("volumeNum", "volume", "volumeClob", "volumeAmm")))
        volume_24hr = _coerce_float(
            _first_present(raw, ("volume24hr", "volume24hrClob", "volume24hrAmm"))
        )
        liquidity = _coerce_float(
            _first_present(raw, ("liquidityNum", "liquidity", "liquidityClob", "liquidityAmm"))
        )
        spread = _coerce_float(_first_present(raw, ("spread", "bidAskSpread")))
        end_date = _first_present(raw, ("endDateIso", "endDate", "umaEndDateIso", "umaEndDate"))
        start_date = _first_present(raw, ("startDateIso", "startDate", "createdAt"))
        updated_at = _first_present(raw, ("updatedAt", "updated_at"))
        url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com"

        return MarketData(
            market_id=market_id,
            slug=slug,
            title=title,
            url=url,
            description=raw.get("description"),
            yes_price=yes_price,
            no_price=no_price,
            volume=volume,
            volume_24hr=volume_24hr,
            liquidity=liquidity,
            spread=spread,
            end_date=end_date,
            start_date=start_date,
            updated_at=updated_at,
            active=is_active_open_market(raw),
            raw=raw,
        )
