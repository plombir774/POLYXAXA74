"""FiveThirtyEight polls client — public CSV, no API key needed.

Provides polling aggregations for political markets — presidential approval,
generic congressional ballot, Senate/House race polls. Useful for markets
like "Will Democrats win the House in 2026?" or "Trump approval rating".

Data source: https://projects.fivethirtyeight.com/polls/data/
Files are public CSVs, updated regularly.

We only fetch a small summary (latest poll average) to keep the prompt small.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from typing import Any

import httpx


logger = logging.getLogger(__name__)

# Base URL for FiveThirtyEight polling CSVs
FTE_BASE_URL = "https://projects.fivethirtyeight.com/polls/data"

# Most useful feeds for Polymarket political markets
FTE_FEEDS: dict[str, str] = {
    "president_approval": f"{FTE_BASE_URL}/approval_topline.csv",
    "generic_ballot": f"{FTE_BASE_URL}/house_generic_ballot_polls.csv",
    "president_polls": f"{FTE_BASE_URL}/president_polls.csv",
    "senate_polls": f"{FTE_BASE_URL}/senate_polls.csv",
    "house_polls": f"{FTE_BASE_URL}/house_polls.csv",
    "governor_polls": f"{FTE_BASE_URL}/governor_polls.csv",
}


# Keywords that hint a market is political.
POLITICAL_KEYWORDS: tuple[str, ...] = (
    "election", "president", "presidential", "senate", "house", "governor",
    "congress", "approval rating", "trump", "biden", "harris", "democrats",
    "republicans", "democratic", "republican", "primary", "midterm",
    "balance of power",
)


def is_political_market(title: str) -> bool:
    title_lower = title.lower()
    return any(kw in title_lower for kw in POLITICAL_KEYWORDS)


def relevant_feeds_for_title(title: str) -> list[str]:
    """Pick relevant FiveThirtyEight feeds based on market title."""
    title_lower = title.lower()
    feeds: list[str] = []
    if "approval" in title_lower or "trump approval" in title_lower or "biden approval" in title_lower:
        feeds.append("president_approval")
    if "generic ballot" in title_lower or "house" in title_lower:
        if "balance of power" in title_lower or "control" in title_lower:
            feeds.append("generic_ballot")
        elif "house" in title_lower:
            feeds.append("house_polls")
    if "senate" in title_lower:
        feeds.append("senate_polls")
    if "governor" in title_lower:
        feeds.append("governor_polls")
    if "president" in title_lower and "approval" not in title_lower:
        feeds.append("president_polls")
    # Default fallback for political markets with no specific match
    if not feeds:
        feeds.extend(["president_approval", "generic_ballot"])
    return list(dict.fromkeys(feeds))


async def _fetch_csv(
    url: str,
    *,
    timeout_seconds: float = 12.0,
) -> list[dict[str, str]] | None:
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds)) as client:
            response = await client.get(url, follow_redirects=True)
            if response.status_code >= 400:
                logger.warning("fte_csv_failed url=%s status=%s", url, response.status_code)
                return None
            text = response.text
    except httpx.HTTPError as exc:
        logger.warning("fte_csv_error url=%s msg=%s", url, str(exc)[:200])
        return None
    try:
        reader = csv.DictReader(io.StringIO(text))
        return list(reader)
    except Exception as exc:
        logger.warning("fte_csv_parse_error url=%s msg=%s", url, str(exc)[:200])
        return None


def _summarize_approval(rows: list[dict[str, str]], limit: int = 5) -> list[str]:
    """Summarize president approval polls."""
    lines: list[str] = []
    # Sort by date desc
    def _date_key(row: dict[str, str]) -> str:
        return row.get("modeldate") or row.get("enddate") or row.get("timestamp") or ""
    rows_sorted = sorted(rows, key=_date_key, reverse=True)
    seen: set[str] = set()
    for row in rows_sorted[:50]:
        subgroup = row.get("subgroup") or "All polls"
        if subgroup in seen:
            continue
        seen.add(subgroup)
        approve = row.get("approve_estimate") or row.get("approve") or ""
        disapprove = row.get("disapprove_estimate") or row.get("disapprove") or ""
        date = _date_key(row)
        if approve and disapprove:
            lines.append(f"  - {subgroup} ({date}): approve {approve}% / disapprove {disapprove}%")
        if len(lines) >= limit:
            break
    return lines


def _summarize_generic_ballot(rows: list[dict[str, str]], limit: int = 3) -> list[str]:
    lines: list[str] = []
    rows_sorted = sorted(rows, key=lambda r: r.get("enddate") or r.get("created_at") or "", reverse=True)
    seen: set[str] = set()
    for row in rows_sorted[:30]:
        pollster = row.get("pollster") or "Unknown"
        end = row.get("enddate") or ""
        dem = row.get("dem") or row.get("democrat") or ""
        rep = row.get("rep") or row.get("republican") or ""
        key = f"{pollster}_{end}"
        if key in seen or not (dem and rep):
            continue
        seen.add(key)
        lines.append(f"  - {pollster} ({end}): D {dem}% / R {rep}%")
        if len(lines) >= limit:
            break
    return lines


def _summarize_race_polls(rows: list[dict[str, str]], race_name: str, limit: int = 5) -> list[str]:
    lines: list[str] = []
    rows_sorted = sorted(rows, key=lambda r: r.get("enddate") or r.get("created_at") or "", reverse=True)
    seen: set[str] = set()
    for row in rows_sorted[:50]:
        pollster = row.get("pollster") or "Unknown"
        end = row.get("enddate") or ""
        key = f"{pollster}_{end}"
        if key in seen:
            continue
        seen.add(key)
        # Generic candidate columns
        cands = []
        for cand_col in ("candidate_name", "candidate", "answer"):
            if cand_col in row and row[cand_col]:
                cands.append(f"{row[cand_col]}={row.get('pct', '')}%")
        if cands:
            lines.append(f"  - {pollster} ({end}): {', '.join(cands[:3])}")
        if len(lines) >= limit:
            break
    return lines


async def fetch_political_context(
    title: str,
    *,
    timeout_seconds: float = 12.0,
) -> str:
    """Fetch relevant FiveThirtyEight polls and format as context for AI prompt."""
    if not is_political_market(title):
        return ""
    feeds = relevant_feeds_for_title(title)
    if not feeds:
        return ""
    # Only fetch the most relevant feed to keep latency low
    feed_key = feeds[0]
    feed_url = FTE_FEEDS.get(feed_key)
    if not feed_url:
        return ""
    rows = await _fetch_csv(feed_url, timeout_seconds=timeout_seconds)
    if not rows:
        return ""
    lines: list[str] = [f"Political polls (FiveThirtyEight, latest):"]
    if feed_key == "president_approval":
        summary = _summarize_approval(rows)
        if summary:
            lines.extend(summary)
        else:
            return ""
    elif feed_key == "generic_ballot":
        summary = _summarize_generic_ballot(rows)
        if summary:
            lines.extend(summary)
        else:
            return ""
    else:
        summary = _summarize_race_polls(rows, feed_key)
        if summary:
            lines.extend(summary)
        else:
            return ""
    return "\n".join(lines)
