"""Wikipedia-based political context provider (V2.4.1).

FiveThirtyEight polling CSVs were discontinued in 2024 (their domain now
redirects to ABC News). This module replaces the FTE-based provider with
a Wikipedia fetch that pulls the latest polling summary from public
Wikipedia articles.

For each political market, we look up the most relevant Wikipedia article
and extract the lead section. The AI then uses this context to reason
about real-world polling / political state.

Wikipedia API is free, no key required. Rate limit: 200 req/s.
Docs: https://www.mediawiki.org/wiki/API:Main_page
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx


logger = logging.getLogger(__name__)

WIKI_BASE_URL = "https://en.wikipedia.org/w/api.php"


# Keywords that hint a market is political.
POLITICAL_KEYWORDS: tuple[str, ...] = (
    "election", "president", "presidential", "senate", "house", "governor",
    "congress", "approval rating", "trump", "biden", "harris", "democrats",
    "republicans", "democratic", "republican", "primary", "midterm",
    "balance of power", "newsom", "vance", "aoc", "ocasio-cortez",
    "sanders", "obama", "Clinton", "desantis",
)


def is_political_market(title: str) -> bool:
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in POLITICAL_KEYWORDS)


def _extract_keywords_from_title(title: str) -> list[str]:
    """Pull candidate names and key terms out of a market title."""
    title_lower = title.lower()
    figures = {
        "trump": "Donald Trump",
        "biden": "Joe Biden",
        "harris": "Kamala Harris",
        "newsom": "Gavin Newsom",
        "desantis": "Ron DeSantis",
        "vance": "JD Vance",
        "aoc": "Alexandria Ocasio-Cortez",
        "ocasio-cortez": "Alexandria Ocasio-Cortez",
        "sanders": "Bernie Sanders",
        "obama": "Barack Obama",
        "clinton": "Hillary Clinton",
        "putin": "Vladimir Putin",
        "macron": "Emmanuel Macron",
        "musk": "Elon Musk",
    }
    found: list[str] = []
    for key, full_name in figures.items():
        if key in title_lower and full_name not in found:
            found.append(full_name)
    return found


def _pick_wikipedia_article(title: str) -> str | None:
    """Pick the most relevant Wikipedia article title to look up."""
    figures = _extract_keywords_from_title(title)
    if figures:
        return figures[0]
    title_lower = title.lower()
    if "2028" in title_lower and ("election" in title_lower or "president" in title_lower):
        return "2028 United States presidential election"
    if "2026" in title_lower and "midterm" in title_lower:
        return "2026 United States elections"
    if "approval" in title_lower and "trump" in title_lower:
        return "Donald Trump"
    if "approval" in title_lower and "biden" in title_lower:
        return "Joe Biden"
    if "senate" in title_lower:
        return "United States Senate"
    if "house" in title_lower and "representatives" not in title_lower:
        return "United States House of Representatives"
    return None


async def _fetch_wiki_extract(
    article_title: str,
    *,
    timeout_seconds: float = 10.0,
) -> str | None:
    """Fetch the lead-section extract of a Wikipedia article."""
    params = {
        "action": "query",
        "titles": article_title,
        "format": "json",
        "prop": "extracts",
        "exintro": 1,
        "explaintext": 1,
        "redirects": 1,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = await client.get(WIKI_BASE_URL, params=params)
            if response.status_code >= 400:
                logger.warning(
                    "wiki_api_failed article=%s status=%s",
                    article_title, response.status_code,
                )
                return None
            data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning(
            "wiki_api_error article=%s msg=%s",
            article_title, str(exc)[:200],
        )
        return None

    pages = data.get("query", {}).get("pages", {})
    if not pages:
        return None
    page = next(iter(pages.values()))
    extract = page.get("extract")
    if not extract or "may refer to:" in extract.lower():
        return None
    return extract[:1200]


def _summarize_extract(article_title: str, extract: str) -> list[str]:
    """Pull 3-5 short bullet-worthy sentences from the Wikipedia extract."""
    if not extract:
        return []
    sentences = re.split(r"(?<=[.!?])\s+", extract)
    picked: list[str] = []
    for s in sentences:
        s = s.strip()
        if not s or len(s) > 220:
            continue
        picked.append(s)
        if len(picked) >= 4:
            break
    return picked


async def fetch_political_context(
    title: str,
    *,
    timeout_seconds: float = 12.0,
) -> str:
    """Fetch relevant Wikipedia context and format it for AI prompt."""
    if not is_political_market(title):
        return ""
    article = _pick_wikipedia_article(title)
    if not article:
        return ""
    extract = await _fetch_wiki_extract(article, timeout_seconds=timeout_seconds)
    if not extract:
        return ""
    summary = _summarize_extract(article, extract)
    if not summary:
        return ""
    lines = [f"Political context (Wikipedia — {article}):"]
    for s in summary:
        lines.append(f"  - {s}")
    return "\n".join(lines)
