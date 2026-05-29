from __future__ import annotations

"""Shared helper: assemble raw indicator dict from all available data sources."""

import json
import logging
from datetime import date

import pandas as pd

from croesus.macro.data_sources.fred_source import FREDSource
from croesus.macro.data_sources.yfinance_macro import YFinanceMacroSource

logger = logging.getLogger(__name__)


def load_raw(
    fred_series: list[str] | None = None,
    include_sentiment: bool = True,
    lookback_years: int = 5,
) -> dict[str, pd.Series]:
    """Fetch and merge data from all macro sources into a single raw dict."""
    raw: dict[str, pd.Series] = {}

    # FRED
    fred = FREDSource()
    fred_data = fred.fetch_series(fred_series or [], lookback_years=lookback_years)
    raw.update(fred_data)

    # yfinance
    yf_source = YFinanceMacroSource()
    yf_data = yf_source.fetch(lookback_years=lookback_years)
    raw.update(yf_data)

    # Sentiment scrapers (weekly, slow — skip if not needed)
    if include_sentiment:
        try:
            from croesus.macro.data_sources.sentiment_scraper import SentimentScraper
            sent_data = SentimentScraper().fetch()
            raw.update(sent_data)
        except Exception as exc:
            logger.warning("Sentiment scraper failed: %s", exc)

    logger.info("Loaded %d macro indicator series", len(raw))
    return raw


def store_macro_state(state, db_path=None) -> None:
    """Persist MacroState to DuckDB macro_scores table."""
    import json
    from croesus.db.connection import get_connection

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO macro_scores
            (date, regime, regime_confidence, growth_direction, inflation_direction,
             amplifier_score, confirmation_score, positioning, raw_indicators, warnings, opportunities)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                str(state.date),
                state.regime,
                state.regime_confidence,
                state.growth_direction,
                state.inflation_direction,
                state.amplifier_score,
                state.confirmation_score,
                state.positioning,
                json.dumps(state.raw_indicators),
                json.dumps(state.warnings),
                json.dumps(state.opportunities),
            ],
        )
