from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict
from typing import Any

import httpx
from pydantic import ValidationError

from app.analysis.classification import classify_market
from app.news.models import NewsScanResult
from app.news.schemas import CatalystAnalysis
from app.polymarket.schemas import AIForecast, MarketData, ScoreBreakdown


logger = logging.getLogger(__name__)
AI_UNAVAILABLE_SUMMARY = "AI analysis is temporarily unavailable, using deterministic analysis only."


class ForecastError(RuntimeError):
    """Raised when an AI forecast cannot be produced or parsed."""


FORECAST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fair_probability_range": {"type": "string"},
        "summary": {"type": "string"},
        "why_interesting": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "verdict": {"type": "string", "enum": ["WATCH", "STRONG_SIGNAL", "AVOID"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": [
        "fair_probability_range",
        "summary",
        "why_interesting",
        "risks",
        "verdict",
        "confidence",
    ],
}

SYSTEM_PROMPT = (
    "You produce cautious prediction-market analysis for a private "
    "personal Telegram bot. This bot is analysis-only. Do not give "
    "financial advice, do not make guaranteed profit claims, do not "
    "tell the user to bet all-in, and always include concrete risks. "
    "If data quality is weak, set confidence to low. Use catalyst "
    "context cautiously: if the scanner is not configured, say news "
    "was not externally checked and do not imply verified catalysts."
)


def build_openai_forecast_request(model: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": [
                    {
                        "type": "input_text",
                        "text": SYSTEM_PROMPT,
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Return only JSON matching the requested schema. Analyze this "
                            "Polymarket market data, including catalyst context when present. "
                            "Do not provide betting instructions or overstate source confidence.\n"
                            f"{json.dumps(payload, default=str)}"
                        ),
                    }
                ],
            },
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "polymarket_forecast",
                "schema": FORECAST_SCHEMA,
                "strict": True,
            }
        },
    }


def recent_price_movement(current: MarketData, previous: MarketData | None) -> str:
    if previous is None or previous.yes_price is None or current.yes_price is None:
        return "No prior snapshot available."
    delta = current.yes_price - previous.yes_price
    return f"{delta:+.2%} yes-price change since the previous snapshot."


def build_forecast_payload(
    market: MarketData,
    signal: ScoreBreakdown,
    risk: ScoreBreakdown,
    previous: MarketData | None = None,
    catalyst: CatalystAnalysis | NewsScanResult | None = None,
    external_context: str | None = None,
) -> dict[str, Any]:
    classification = classify_market(market, signal_score=signal.total)
    catalyst_context: dict[str, Any]
    if catalyst is None:
        catalyst_context = {
            "scanner_not_configured": True,
            "fresh_catalyst": "unknown",
            "possible_catalyst": "scanner not configured",
            "source_confidence": "low",
            "market_reaction": "unclear",
            "notes": ["External news scanner is not configured. Using market data only."],
        }
    elif isinstance(catalyst, NewsScanResult):
        catalyst_context = asdict(catalyst)
    else:
        catalyst_context = catalyst.model_dump(mode="json")
    payload = {
        "market_title": market.title,
        "market_description": market.description,
        "market_type": classification.display,
        "market_type_reasons": list(classification.reasons),
        "catalyst_context": catalyst_context,
        "current_yes_price": market.yes_price,
        "current_no_price": market.no_price,
        "volume": market.volume,
        "volume_24hr": market.volume_24hr,
        "liquidity": market.liquidity,
        "spread": market.spread,
        "end_date": market.end_date.isoformat() if market.end_date else None,
        "recent_price_movement": recent_price_movement(market, previous),
        "deterministic_signal_score": signal.total,
        "deterministic_signal_components": signal.components,
        "deterministic_risk_score": risk.total,
        "deterministic_risk_components": risk.components,
    }
    if external_context and external_context.strip():
        payload["external_context"] = external_context.strip()
    return payload


def _extract_output_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    chunks: list[str] = []
    for item in response.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str):
                chunks.append(text)
    return "\n".join(chunks).strip()


def _parse_json_text(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ForecastError("AI forecast was not valid JSON") from exc
    if not isinstance(data, dict):
        raise ForecastError("AI forecast JSON must be an object")
    return data


def _short_sanitized_message(exc: BaseException) -> str:
    message = str(exc).replace("\n", " ").strip()
    message = re.sub(r"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]+", "sk-***MASKED***", message)
    if len(message) > 240:
        return f"{message[:237]}..."
    return message or exc.__class__.__name__


def _log_openai_failure(model: str, exc: BaseException, status_code: int | None = None) -> None:
    logger.warning(
        "openai_forecast_failure model=%s error_class=%s status_code=%s message=%s",
        model,
        exc.__class__.__name__,
        status_code,
        _short_sanitized_message(exc),
    )


class OpenAIForecastClient:
    def __init__(
        self,
        api_key: str | None,
        model: str = "gpt-5.5",
        timeout_seconds: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = httpx.Timeout(timeout_seconds)

    async def generate(
        self,
        market: MarketData,
        signal: ScoreBreakdown,
        risk: ScoreBreakdown,
        previous: MarketData | None = None,
        catalyst: CatalystAnalysis | NewsScanResult | None = None,
        external_context: str | None = None,
    ) -> AIForecast:
        if not self.api_key:
            raise ForecastError("OPENAI_API_KEY is not configured")

        payload = build_forecast_payload(
            market,
            signal,
            risk,
            previous,
            catalyst,
            external_context=external_context,
        )
        body = build_openai_forecast_request(self.model, payload)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=body,
                )
                response.raise_for_status()
                response_json = response.json()
        except httpx.HTTPStatusError as exc:
            _log_openai_failure(self.model, exc, exc.response.status_code)
            raise ForecastError(AI_UNAVAILABLE_SUMMARY) from exc
        except httpx.HTTPError as exc:
            _log_openai_failure(self.model, exc)
            raise ForecastError(AI_UNAVAILABLE_SUMMARY) from exc
        except json.JSONDecodeError as exc:
            _log_openai_failure(self.model, exc)
            raise ForecastError(AI_UNAVAILABLE_SUMMARY) from exc

        text = _extract_output_text(response_json)
        try:
            data = _parse_json_text(text)
            return AIForecast.model_validate(data)
        except (ForecastError, ValidationError) as exc:
            _log_openai_failure(self.model, exc)
            raise ForecastError(AI_UNAVAILABLE_SUMMARY) from exc


def deterministic_forecast_fallback(
    signal: ScoreBreakdown,
    risk: ScoreBreakdown,
    reason: str = AI_UNAVAILABLE_SUMMARY,
) -> AIForecast:
    if risk.total > 70:
        verdict = "AVOID"
    elif signal.total >= 80 and risk.total <= 50:
        verdict = "STRONG_SIGNAL"
    elif signal.total >= 65 and risk.total <= 65:
        verdict = "WATCH"
    else:
        verdict = "WATCH"
    return AIForecast(
        fair_probability_range="unknown",
        summary=AI_UNAVAILABLE_SUMMARY,
        why_interesting=[
            f"Signal score: {signal.total}/100.",
            "The deterministic model can still run without AI output.",
        ],
        risks=[
            f"Risk score: {risk.total}/100.",
            "AI analysis is unavailable, so confidence is low.",
        ],
        verdict=verdict,
        confidence="low",
    )
