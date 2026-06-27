import pytest

from app.polymarket.client import (
    MarketNotFoundError,
    PolymarketClient,
    is_active_open_market,
    select_highest_volume_active_market,
)


def market_raw(
    slug: str,
    question: str,
    volume: float,
    *,
    active: bool = True,
    closed: bool = False,
    event_slug: str | None = None,
) -> dict:
    raw = {
        "id": slug,
        "slug": slug,
        "question": question,
        "volumeNum": volume,
        "liquidityNum": 10_000,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": "[0.55, 0.45]",
        "active": active,
        "closed": closed,
    }
    if event_slug:
        raw["events"] = [{"slug": event_slug}]
    return raw


@pytest.mark.asyncio
async def test_direct_slug_lookup_falls_back_to_active_market_search(monkeypatch) -> None:
    client = PolymarketClient()
    calls = []

    async def fake_request_json(
        base_url,
        path,
        params=None,
        *,
        strategy="request",
        parsed_slug=None,
        log_context=None,
    ):
        calls.append((path, strategy))
        if path == "/markets/slug/new-rihanna-album-before-gta-vi":
            return 404, None
        if path == "/markets":
            return 200, [
                market_raw(
                    "new-rihanna-album-before-gta-vi",
                    "New Rihanna album before GTA VI?",
                    50_000,
                )
            ]
        return 404, None

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    market = await client.fetch_market_by_slug(
        "new-rihanna-album-before-gta-vi",
        enrich=False,
        log_context="forecast",
    )

    assert market.slug == "new-rihanna-album-before-gta-vi"
    assert calls[0] == ("/markets/slug/new-rihanna-album-before-gta-vi", "direct_market_slug")
    assert ("/markets", "active_markets_search") in calls


def test_select_highest_volume_active_market_from_multiple_candidates() -> None:
    selected = select_highest_volume_active_market(
        [
            market_raw("closed-market", "Closed market", 1_000_000, closed=True),
            market_raw("low-volume", "Low volume", 5_000),
            market_raw("high-volume", "High volume", 250_000),
        ]
    )

    assert selected is not None
    assert selected["slug"] == "high-volume"


def test_unresolved_status_is_still_active() -> None:
    raw = market_raw("unresolved-market", "Unresolved market", 10_000)
    raw["umaResolutionStatus"] = "unresolved"
    assert is_active_open_market(raw) is True


@pytest.mark.asyncio
async def test_event_slug_selects_highest_volume_active_market(monkeypatch) -> None:
    client = PolymarketClient()

    async def fake_request_json(
        base_url,
        path,
        params=None,
        *,
        strategy="request",
        parsed_slug=None,
        log_context=None,
    ):
        if path == "/markets/slug/what-will-happen-before-gta-vi":
            return 404, None
        if path == "/markets":
            return 200, []
        if path == "/events/slug/what-will-happen-before-gta-vi":
            return 200, {
                "slug": "what-will-happen-before-gta-vi",
                "markets": [
                    market_raw("inactive-candidate", "Inactive", 1_000_000, active=False),
                    market_raw("lower-volume", "Lower volume", 10_000),
                    market_raw("higher-volume", "Higher volume", 80_000),
                ],
            }
        return 404, None

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    market = await client.fetch_market_for_input(
        "what-will-happen-before-gta-vi",
        "event",
        enrich=False,
    )

    assert market.slug == "higher-volume"


@pytest.mark.asyncio
async def test_market_not_found_after_all_lookup_strategies(monkeypatch) -> None:
    client = PolymarketClient()

    async def fake_request_json(
        base_url,
        path,
        params=None,
        *,
        strategy="request",
        parsed_slug=None,
        log_context=None,
    ):
        if path == "/markets":
            return 200, []
        return 404, None

    monkeypatch.setattr(client, "_request_json", fake_request_json)

    with pytest.raises(MarketNotFoundError):
        await client.fetch_market_by_slug("missing-market", enrich=False)
