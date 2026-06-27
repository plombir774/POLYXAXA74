"""External data sources module (V2.4).

Provides free, real-world context for AI forecasts:
- CoinGecko: crypto spot prices + 24h change (no key needed)
- FRED: macro indicators — Fed funds rate, CPI, unemployment, treasury yields (free key)
- FiveThirtyEight: political polling data (no key needed, public CSV)

Each provider returns a small text snippet that gets injected into the
OpenAI forecast prompt so the model can reason about real-world context
instead of relying solely on market price data.

Dune Analytics integration is stubbed — needs SQL queries per market type,
will be wired up in V2.5.
"""
