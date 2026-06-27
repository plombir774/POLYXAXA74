"""FRED (Federal Reserve Economic Data) client — free API key.

Provides macro indicators for markets about Fed rates, CPI, GDP, unemployment,
treasury yields, etc. Useful for markets like "Will Fed cut rates in July?"
or "Will CPI print above 3%?".

Get a free API key: https://fred.stlouisfed.org/docs/api/api_key.html
Free tier: 120 requests/min.

Docs: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
"""
from __future__ import annotations

import logging
from typing import Any

import httpx


logger = logging.getLogger(__name__)

FRED_BASE_URL = "https://api.stlouisfed.org/fred"

# Map of FRED series id → human label + category hint.
# These are the most commonly referenced macro indicators on Polymarket.
FRED_SERIES: dict[str, dict[str, str]] = {
    # Interest rates
    "FEDFUNDS": {"label": "Fed Funds Rate (%)", "category": "rates"},
    "DGS10": {"label": "10-Year Treasury Yield (%)", "category": "rates"},
    "DGS2": {"label": "2-Year Treasury Yield (%)", "category": "rates"},
    "DGS30": {"label": "30-Year Treasury Yield (%)", "category": "rates"},
    "T10YIE": {"label": "10-Year Breakeven Inflation (%)", "category": "rates"},
    # Inflation
    "CPIAUCSL": {"label": "CPI (All Urban, SA)", "category": "inflation"},
    "CPILFESL": {"label": "Core CPI (SA)", "category": "inflation"},
    "PCEPI": {"label": "PCE Price Index", "category": "inflation"},
    "PCEPILFE": {"label": "Core PCE Price Index", "category": "inflation"},
    # Employment / growth
    "UNRATE": {"label": "Unemployment Rate (%)", "category": "employment"},
    "PAYEMS": {"label": "Nonfarm Payrolls (thousands)", "category": "employment"},
    "GDP": {"label": "GDP (billions $)", "category": "growth"},
    "A191RL1Q225SBEA": {"label": "GDP Growth Rate (% QoQ annualized)", "category": "growth"},
    # Recession indicator
    "USREC": {"label": "NBER Recession Indicator", "category": "recession"},
}


# Keywords that hint a market is macro-related.
MACRO_KEYWORDS: tuple[str, ...] = (
    "fed", "fomc", "interest rate", "rate cut", "rate hike", "treasury",
    "yield", "inflation", "cpi", "pce", "gdp", "recession", "unemployment",
    "jobs report", "nonfarm", "non-farm", "powell", "federal reserve",
    "soft landing", "hard landing", "rate decision",
)


def is_macro_market(title: str) -> bool:
    """Quick check if a market title mentions a macro indicator."""
    title_lower = title.lower()
    return any(kw in title_lower for kw in MACRO_KEYWORDS)


def relevant_series_for_title(title: str) -> list[str]:
    """Pick the most relevant FRED series based on market title."""
    title_lower = title.lower()
    picks: list[str] = []
    if any(kw in title_lower for kw in ("fed funds", "rate cut", "rate hike", "powell", "fomc", "federal reserve")):
        picks.extend(["FEDFUNDS", "DGS2", "DGS10"])
    if "cpi" in title_lower or "inflation" in title_lower:
        picks.extend(["CPIAUCSL", "CPILFESL", "T10YIE"])
    if "pce" in title_lower:
        picks.extend(["PCEPI", "PCEPILFE"])
    if "unemployment" in title_lower or "jobs" in title_lower or "nonfarm" in title_lower or "non-farm" in title_lower:
        picks.extend(["UNRATE", "PAYEMS"])
    if "gdp" in title_lower or "recession" in title_lower:
        picks.extend(["GDP", "USREC", "A191RL1Q225SBEA"])
    if "treasury" in title_lower or "yield" in title_lower:
        picks.extend(["DGS2", "DGS10", "DGS30"])
    # Deduplicate preserving order
    return list(dict.fromkeys(picks)) or ["FEDFUNDS", "CPIAUCSL", "UNRATE"]


async def fetch_fred_series(
    series_id: str,
    api_key: str,
    *,
    timeout_seconds: float = 10.0,
    limit: int = 5,
) -> dict[str, Any] | None:
    """Fetch latest observations for a FRED series.

    Returns: {"series_id": str, "label": str, "observations": [{"date": str, "value": str}, ...]}
    or None on error.
    """
    if not api_key:
        return None
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": limit,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = await client.get(
                f"{FRED_BASE_URL}/series/observations",
                params=params,
            )
            if response.status_code >= 400:
                logger.warning(
                    "fred_api_failed series=%s status=%s body=%s",
                    series_id,
                    response.status_code,
                    response.text[:200],
                )
                return None
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "fred_api_error series=%s error_class=%s msg=%s",
            series_id,
            exc.__class__.__name__,
            str(exc)[:200],
        )
        return None

    observations = data.get("observations", [])
    if not observations:
        return None
    return {
        "series_id": series_id,
        "label": FRED_SERIES.get(series_id, {}).get("label", series_id),
        "observations": [
            {"date": obs.get("date"), "value": obs.get("value")}
            for obs in observations[:limit]
            if obs.get("value") not in (None, ".", "")
        ],
    }


async def fetch_macro_context(
    title: str,
    api_key: str | None,
    *,
    timeout_seconds: float = 10.0,
) -> str:
    """Fetch relevant FRED series and format as context for AI prompt.

    Returns an empty string if api_key is missing or no relevant series found.
    """
    if not api_key:
        return ""
    if not is_macro_market(title):
        return ""
    import asyncio
    series_ids = relevant_series_for_title(title)[:4]  # cap to 4 series per query
    tasks = [fetch_fred_series(sid, api_key, timeout_seconds=timeout_seconds) for sid in series_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    lines: list[str] = ["Macro indicators (FRED, latest values):"]
    any_data = False
    for result in results:
        if isinstance(result, Exception) or not result:
            continue
        any_data = True
        label = result["label"]
        obs = result["observations"]
        if not obs:
            continue
        latest = obs[0]
        prev = obs[1] if len(obs) > 1 else None
        line = f"- {label} ({latest['date']}): {latest['value']}"
        if prev and prev["value"] not in (None, "."):
            try:
                delta = float(latest["value"]) - float(prev["value"])
                if abs(delta) > 0.001:
                    sign = "+" if delta >= 0 else ""
                    line += f" (prev {prev['value']}, {sign}{delta:.3f})"
            except (ValueError, TypeError):
                pass
        lines.append(line)
    if not any_data:
        return ""
    return "\n".join(lines)
