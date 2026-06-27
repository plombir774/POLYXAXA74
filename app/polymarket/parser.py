from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import unquote, urlparse


SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
URL_DETECTED_MARKER = "://"


class MarketParseError(ValueError):
    """Raised when a Polymarket URL or slug cannot be normalized."""


@dataclass(frozen=True)
class ParsedMarketInput:
    slug: str
    input_type: str
    raw: str


def _looks_like_url_or_slug(raw: str) -> bool:
    """True if the input looks like a URL or a slug — i.e. safe to parse directly.

    A free-text market question like "Will Bitcoin hit $150k by June 30, 2026?"
    will contain spaces, question marks, or other characters that disqualify it
    from being a slug or URL. Such inputs are routed to a text-query search.
    """
    if not raw:
        return False
    if URL_DETECTED_MARKER in raw:
        return True
    candidate = raw.strip().strip("/")
    if "/" in candidate and "://" not in raw:
        parts = [p for p in candidate.split("/") if p]
        return all(SLUG_RE.fullmatch(p) for p in parts)
    return bool(SLUG_RE.fullmatch(candidate))


def parse_market_input(value: str) -> ParsedMarketInput:
    raw = (value or "").strip()
    if not raw:
        raise MarketParseError("Market URL or slug is required")

    if not _looks_like_url_or_slug(raw):
        return ParsedMarketInput(slug=raw, input_type="text_query", raw=raw)

    candidate = raw
    input_type = "slug"
    if "://" in raw:
        parsed = urlparse(raw)
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        candidate = ""
        for marker in ("event", "market", "markets"):
            if marker in parts:
                index = parts.index(marker)
                if index + 1 < len(parts):
                    candidate = parts[index + 1]
                    input_type = "event" if marker == "event" else "market"
                    break
        if not candidate and parts:
            candidate = parts[-1]
    elif "/" in raw:
        parts = [unquote(part) for part in raw.split("/") if part]
        for marker in ("event", "market", "markets"):
            if marker in parts:
                input_type = "event" if marker == "event" else "market"
                break
        candidate = parts[-1] if parts else ""

    candidate = candidate.strip().strip("/")
    if "?" in candidate:
        candidate = candidate.split("?", 1)[0]
    if "#" in candidate:
        candidate = candidate.split("#", 1)[0]

    if not candidate or not SLUG_RE.fullmatch(candidate):
        raise MarketParseError("Could not parse a valid Polymarket slug")
    return ParsedMarketInput(slug=candidate, input_type=input_type, raw=raw)


def parse_market_slug(value: str) -> str:
    return parse_market_input(value).slug
