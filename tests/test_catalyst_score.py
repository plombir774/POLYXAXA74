from datetime import UTC, datetime, timedelta

from app.news.models import NewsItem
from app.news.scanner import calculate_catalyst_score, catalyst_strength_label


def item(hours_ago: int) -> NewsItem:
    return NewsItem(
        title="Bitcoin ETF approval discussion",
        url=f"https://example.com/{hours_ago}",
        source="Reuters",
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        summary="BTC inflows surge.",
    )


def test_catalyst_score_low() -> None:
    score = calculate_catalyst_score([item(80)], [10], sentiment_confidence=0)
    assert score <= 20
    assert catalyst_strength_label(score) == "weak"


def test_catalyst_score_medium() -> None:
    score = calculate_catalyst_score([item(20), item(25)], [45, 50], sentiment_confidence=40)
    assert 21 <= score <= 60


def test_catalyst_score_high() -> None:
    score = calculate_catalyst_score(
        [item(1), item(2), item(3), item(4)],
        [85, 90, 80, 75],
        sentiment_confidence=90,
    )
    assert score >= 70
