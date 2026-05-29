from __future__ import annotations

import logging
import os
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# All FRED series used by the macro engine, grouped by update cadence
DAILY_SERIES = [
    "T5YIE",       # 5Y Breakeven Inflation
    "DCOILWTICO",  # WTI Crude
    "EFFR",        # Fed Funds Rate
    "DGS2",        # 2Y Treasury
    "DGS10",       # 10Y Treasury
    "T10Y2Y",      # Yield Curve (10Y-2Y)
    "DFII10",      # Real Rate (TIPS 10Y)
    "BAMLH0A0HYM2",# HY Spread
    "BAMLC0A0CM",  # IG Spread
    "RRPONTSYD",   # Overnight Reverse Repo
]
WEEKLY_SERIES = [
    "ICSA",    # Initial Jobless Claims
    "WALCL",   # Fed Balance Sheet
    "WTREGEN", # TGA
    "NFCI",    # NFCI
]
MONTHLY_SERIES = [
    # MANEAPUSA (ISM Manufacturing PMI) was removed from FRED in June 2016
    # due to a licensing dispute. Use croesus.macro.data_sources.ism_scraper
    # for ISM PMI data. CFNAI is the reliable FRED-based activity proxy.
    "CFNAI",         # Chicago Fed National Activity Index (85-indicator composite)
    "UNRATE",        # Unemployment Rate
    "RSXFS",         # Retail Sales
    "INDPRO",        # Industrial Production
    "CPILFESL",      # Core CPI
    "PCEPILFE",      # Core PCE
    "CES0500000003", # Wage Growth
    "M2SL",          # M2
]
QUARTERLY_SERIES = [
    "GDPC1",    # Real GDP
    "DRTSCILM", # Bank Lending Standards
]

ALL_SERIES = DAILY_SERIES + WEEKLY_SERIES + MONTHLY_SERIES + QUARTERLY_SERIES


class FREDSource:
    """
    Thin wrapper around the FRED API using the `fredapi` library.

    Requires FRED_API_KEY environment variable or explicit api_key argument.
    Gracefully returns empty dict on missing key or network errors.
    """

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("FRED_API_KEY", "")

    def _client(self):
        try:
            from fredapi import Fred  # optional dep
        except ImportError as exc:
            raise ImportError(
                "fredapi is required for FRED data. Install with: pip install fredapi"
            ) from exc
        return Fred(api_key=self._api_key)

    def fetch_series(
        self,
        series_ids: list[str],
        lookback_years: int = 5,
    ) -> dict[str, pd.Series]:
        """
        Fetch multiple FRED series covering `lookback_years` of history.

        Returns a dict {series_id: pd.Series} for successfully fetched series.
        Failed series are logged and skipped.
        """
        if not self._api_key:
            logger.warning("FRED_API_KEY not set — skipping FRED data sources")
            return {}

        try:
            fred = self._client()
        except ImportError as exc:
            logger.warning("fredapi not installed: %s", exc)
            return {}

        start = (date.today() - timedelta(days=lookback_years * 366)).isoformat()
        result: dict[str, pd.Series] = {}

        for sid in series_ids:
            try:
                s = fred.get_series(sid, observation_start=start)
                if s is not None and len(s):
                    result[sid] = s.astype(float)
                    logger.debug("FRED %s: %d rows", sid, len(s))
            except Exception as exc:
                logger.warning("FRED %s failed: %s", sid, exc)

        return result

    def fetch_all(self, lookback_years: int = 5) -> dict[str, pd.Series]:
        return self.fetch_series(ALL_SERIES, lookback_years=lookback_years)
