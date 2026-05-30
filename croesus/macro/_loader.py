from __future__ import annotations

"""Shared helper: assemble raw indicator dict from all available data sources."""

import json
import logging

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

    # ISM PMI scrapers (manufacturing + services)
    # ISM data was removed from FRED in June 2016; CFNAI is the FRED-based fallback.
    try:
        from croesus.macro.data_sources.ism_scraper import ISMScraper
        ism_data = ISMScraper().fetch()
        raw.update(ism_data)
        if ism_data:
            logger.info("ISM scraper: loaded %s", list(ism_data.keys()))
    except Exception as exc:
        logger.warning("ISM scraper failed: %s", exc)

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


def _as_dict(value):
    """Deserialize a DuckDB JSON column that may arrive as str or already-parsed."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def load_latest_macro_state(conn):
    """
    Load the most recent MacroState from the macro_scores table.

    Returns the MacroState for the latest stored date, or None if the table
    is empty or absent. Reuses the caller's connection so the daily pipeline
    does not open a second handle to the same DuckDB file.
    """
    from croesus.macro.models import MacroState

    try:
        row = conn.execute(
            """
            SELECT date, regime, regime_confidence, growth_direction,
                   inflation_direction, amplifier_score, confirmation_score,
                   positioning, raw_indicators, warnings, opportunities,
                   regime_methods
            FROM macro_scores
            ORDER BY date DESC
            LIMIT 1
            """
        ).fetchone()
    except Exception as exc:  # table missing (pre-migration) etc.
        logger.warning("Could not read macro_scores: %s", exc)
        return None

    if row is None:
        return None

    return MacroState(
        date=row[0],
        regime=row[1],
        regime_confidence=row[2],
        growth_direction=row[3],
        inflation_direction=row[4],
        amplifier_score=row[5],
        confirmation_score=row[6],
        positioning=row[7],
        raw_indicators=_as_dict(row[8]) or {},
        warnings=_as_dict(row[9]) or [],
        opportunities=_as_dict(row[10]) or [],
        regime_methods=_as_dict(row[11]) or {},
    )


def store_macro_state(state, db_path=None) -> None:
    """Persist MacroState to DuckDB macro_scores table."""
    from croesus.db.connection import get_connection

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO macro_scores
            (date, regime, regime_confidence, growth_direction, inflation_direction,
             amplifier_score, confirmation_score, positioning, raw_indicators,
             warnings, opportunities, regime_methods)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(state.regime_methods),
            ],
        )
