# Personal Polymarket Forecast Bot V2.2 Edge Calibration V2

A private Telegram bot for personal Polymarket market analysis. It fetches public market data, scores signals and risks deterministically, optionally asks the OpenAI API for a cautious JSON forecast, and sends Telegram commands, alerts, and daily digests.

This project is analysis-only. It does not place trades, does not bypass geoblocking, does not implement automated order execution, and does not make guaranteed profit claims.

## Features

- `/top` shows active Polymarket markets ranked by activity, with default meme filtering.
- `/top crypto`, `/top politics`, `/top macro`, `/top sports`, and `/top all` apply focused filters.
- `/edge`, `/edge crypto`, `/edge politics`, `/edge macro`, and `/edge sports` scan a larger market universe and rank estimated opportunities.
- `/calibration` shows tracked edge predictions, resolved outcomes, accuracy, and category reliability.
- `/status` shows bot health, database status, watchlist count, API status, runtime thresholds, news scanner configuration, opportunity scan metrics, and calibration metrics.
- `/watch <market_url_or_slug>` saves a market to SQLite.
- `/watchlist` shows watched markets and latest snapshots.
- `/unwatch <id>` disables a watched market.
- `/forecast <market_url_or_slug>` returns market type, movement history, catalyst check, deterministic signal/risk scores, edge read, and an AI forecast when configured.
- `/catalyst <market_url_or_slug>` runs the same market lookup and reports recent catalyst context when a news/search provider is configured.
- `/daily` summarizes strong watched signals, interesting watched markets, high-volume active markets, avoid/risky markets, and meme/lottery markets to ignore, with concise catalyst notes.
- `/daily` also includes top ranked opportunities from the opportunity scanner and model performance metrics.
- `/signals` shows the last 10 generated signals.
- Scheduled watchlist checks send alerts when the signal score reaches `MIN_SIGNAL_SCORE`.

## Setup

Requirements:

- Python 3.11+
- A Telegram bot token from BotFather
- Your Telegram numeric user id
- Optional OpenAI API key for AI forecasts

Install dependencies:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Create a local `.env` from `.env.example`:

```bash
copy .env.example .env
```

Fill in:

```env
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_ID=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.5
NEWS_PROVIDER=newsapi
NEWS_API_KEY=
NEWS_LOOKBACK_HOURS=24
NEWS_MAX_RESULTS=5
DATABASE_URL=sqlite:///polymarket_bot.db
CHECK_INTERVAL_MINUTES=60
MIN_SIGNAL_SCORE=70
MEME_ALLOW_VOLUME_THRESHOLD=10000000
```

`MEME_ALLOW_VOLUME_THRESHOLD` controls when default `/top` may include obvious meme markets. Use `/top all` to inspect unfiltered markets.

News scanner:

- `NEWS_PROVIDER=newsapi` uses NewsAPI when `NEWS_API_KEY` is present.
- If `NEWS_PROVIDER=newsapi` but `NEWS_API_KEY` is empty, the bot automatically falls back to the RSS provider.
- `NEWS_PROVIDER=rss` uses public RSS feeds from Reuters, CNBC, CoinDesk, Cointelegraph, and Bloomberg-style market feeds.
- `NEWS_PROVIDER=none` disables external news requests and keeps the market-data-only message.
- `NEWS_LOOKBACK_HOURS` and `NEWS_MAX_RESULTS` control the catalyst scan window and result count.

Top categories:

- `/top` uses default filtering and suppresses obvious meme markets below the meme volume threshold.
- `/top all` shows all active markets returned by the active-market fetch.
- `/top crypto`, `/top politics`, `/top macro`, and `/top sports` filter by Gamma metadata when available, then fall back to title/question/description keywords.
- Explicit `/top` categories are strict: if the fetched batch has no matching category results, the bot reports no matches instead of showing unfiltered markets.
- Unknown categories return a short usage hint.

Opportunity scanner:

- `/edge` scans roughly 100-300 active/high-volume plus watched markets, not just the top 10.
- `/edge crypto`, `/edge politics`, `/edge macro`, and `/edge sports` apply category filtering before ranking.
- Results are cached for 15 minutes to avoid excessive API calls.
- `/status` reports the last edge scan timestamp, markets scanned, qualified opportunities, tracked predictions, resolved predictions, overall accuracy, and best category.
- Successful `/edge` scans store the top opportunities in `opportunity_history`, with same-day duplicate protection.
- A daily resolution updater checks unresolved predictions and stores YES/NO outcomes when markets resolve.
- `/calibration` summarizes reliability by category so future edge estimates and confidence scores can be calibrated from historical results.

## Run Locally

```bash
python run.py
```

The bot uses Telegram polling. Only `TELEGRAM_ALLOWED_USER_ID` can use commands; everyone else receives `This is a private bot.`

## Deploy

A simple deployment path is a small VPS or home server:

1. Clone or copy the project.
2. Install Python 3.11+ and dependencies.
3. Create `.env` with production values.
4. Run `python run.py` under a process manager such as systemd, NSSM, Docker, or a supervised terminal session.
5. Back up `polymarket_bot.db` if you care about watchlist/snapshot history.

## Data Sources

The bot uses public Polymarket endpoints only:

- Gamma API for market discovery and market metadata.
- CLOB public endpoints for midpoint and spread when token ids are available.
- NewsAPI when configured with `NEWS_API_KEY`.
- RSS fallback when NewsAPI is not configured.

No authenticated trading endpoints are used.

## Catalyst Scanner

Forecast Quality V2.1 adds a real catalyst layer. It builds market-aware queries, fetches external news, deduplicates articles by title and URL, scores relevance, runs rules-based sentiment, and calculates a 0-100 catalyst score.

Provider behavior:

- `newsapi`: NewsAPI `/v2/everything`.
- `rss`: public RSS fallback.
- `none`: disabled scanner.

Catalyst score interpretation:

- `0-20`: weak
- `21-50`: moderate
- `51-75`: strong
- `76-100`: major

When no provider is configured, the bot is deterministic and transparent:

```text
Catalyst check
External news scanner is not configured. Using market data only.
```

When a provider is configured, `/forecast` shows catalyst score, sentiment, confidence, and recent catalyst headlines. `/catalyst` shows top articles with source and age. `/daily` adds a Strong catalyst markets section.

## Scoring

Signal score is 0-100. Forecast Quality V2 rewards useful activity and penalizes poor market quality:

- Price movement: 0-25
- Volume spike: 0-20
- Liquidity: 0-15
- Spread: 0-15
- Time to resolution: 0-10
- Freshness/activity: 0-10
- AI confidence placeholder: 0-5
- Market quality penalties for meme, lottery, extreme probability, ambiguous resolution, very low liquidity, high-volume/no-edge, and short-deadline markets

Risk score is 0-100:

- Low liquidity
- Wide spread
- Close to resolution
- Unclear market title/rules
- Extreme price
- Missing data
- Meme, lottery, and ambiguous-resolution penalties

Verdicts:

- `STRONG SIGNAL`: score >= 80 and risk <= 50
- `WATCH`: score >= 65 and risk <= 65
- `INTERESTING BUT RISKY`: some signal, but elevated risk or weak quality
- `HIGH VOLUME / NO EDGE`: high-volume market without enough deterministic edge
- `LOTTERY STYLE`: extreme probability or lottery-like payoff profile
- `AVOID`: high risk or poor market quality
- `LOW PRIORITY`: everything else

Market type labels:

- `NORMAL`
- `MEME`
- `LOTTERY`
- `LOW_LIQUIDITY`
- `EXTREME_PROBABILITY`
- `AMBIGUOUS_RESOLUTION`
- `HIGH_VOLUME_NO_EDGE`
- `SHORT_DEADLINE`
- `NEWS_DRIVEN`

## Opportunity Ranking

Opportunity Scanner V2 estimates where market probability may differ from a calibrated fair probability band. It is analysis-only and does not place or suggest orders.

Quality filters reject meme/joke markets, duplicates, low-liquidity markets, lottery/extreme-probability markets, and huge spreads.

Ranking combines:

- Edge estimate: 40%
- Market quality: 25%
- Confidence: 20%
- Risk adjustment: 15%

The output shows market price, estimated fair range, edge, quality, confidence, risk, and a concise reason.

Calibration:

- Every successful `/edge` scan records top-ranked predictions with market probability, fair probability midpoint, edge estimate, quality, confidence, and risk.
- Resolved predictions are scored as YES/NO outcomes.
- `/calibration`, `/status`, and `/daily` expose prediction count, resolved count, overall accuracy, best category, and worst category.
- Category reliability adjusts future fair probability estimates and caps confidence scores so confidence does not stay unrealistically pinned at 100.
- Catalyst matching uses stronger market-specific relevance checks so unrelated articles are not attached just because they share a broad topic.

## Tests

```bash
pytest
```

Tests cover scoring, risk, market classification, top filtering, opportunity scanning/ranking/filtering/edge estimation, calibration, opportunity history storage, resolution updates, confidence caps, catalyst relevance filtering, snapshot movement, repository add/remove/snapshots/signal history, URL/slug parsing, status data, digest formatting, provider fallback, query generation, news deduplication, relevance scoring, sentiment, catalyst scoring, OpenAI catalyst payload safety, provider failure handling, and secret masking.

## Disclaimer

This bot is for private personal research. It is not financial advice, does not guarantee profits, does not automate trades, and should not be used to bypass any legal, geographic, platform, or account restrictions.

<!-- watcher live-test marker 2026-06-28T07:37:56Z -->
