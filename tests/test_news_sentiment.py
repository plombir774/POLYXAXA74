from app.news.sentiment import analyze_news_sentiment


def test_bullish_sentiment() -> None:
    assert analyze_news_sentiment("ETF approval sparks growth and record inflows") == "bullish"


def test_bearish_sentiment() -> None:
    assert analyze_news_sentiment("New ban and lawsuit follow market crash") == "bearish"


def test_neutral_sentiment() -> None:
    assert analyze_news_sentiment("Officials meet before a scheduled update") == "neutral"

