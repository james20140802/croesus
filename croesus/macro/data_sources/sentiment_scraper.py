from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def _fetch_aaii() -> pd.Series | None:
    """
    Attempt to scrape AAII Bull-Bear spread from aaii.com.

    Returns a Series indexed by date, or None on failure.
    AAII publishes a CSV at a predictable URL; structure may change.
    """
    try:
        import requests  # stdlib-like; already a transitive dep

        url = "https://www.aaii.com/files/surveys/sentiment.xls"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()

        import io
        df = pd.read_excel(io.BytesIO(resp.content), skiprows=3, engine="xlrd")
        # Typical columns: Reported Date, Bullish, Neutral, Bearish, ...
        date_col = df.columns[0]
        bull_col = [c for c in df.columns if "Bull" in str(c)]
        bear_col = [c for c in df.columns if "Bear" in str(c)]
        if not bull_col or not bear_col:
            raise ValueError("AAII column names not as expected")
        df = df[[date_col, bull_col[0], bear_col[0]]].dropna()
        df.columns = ["date", "bullish", "bearish"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        spread = (df["bullish"] - df["bearish"]).astype(float)
        spread.name = "aaii_bull_bear"
        return spread
    except Exception as exc:
        logger.warning("AAII scraper failed: %s", exc)
        return None


def _fetch_naaim() -> pd.Series | None:
    """
    Attempt to scrape NAAIM Exposure Index from naaim.org.

    Returns a Series indexed by date, or None on failure.
    """
    try:
        import requests

        url = "https://www.naaim.org/programs/naaim-exposure-index/naaim-exposure-index-data/"
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, timeout=15, headers=headers)
        resp.raise_for_status()

        tables = pd.read_html(resp.text)
        if not tables:
            raise ValueError("No tables found on NAAIM page")
        df = tables[0]
        # Typical columns: Date, NAAIM Number, ...
        date_col = df.columns[0]
        val_col = df.columns[1]
        df = df[[date_col, val_col]].dropna()
        df.columns = ["date", "naaim"]
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").set_index("date")
        series = df["naaim"].astype(float)
        series.name = "naaim_exposure"
        return series
    except Exception as exc:
        logger.warning("NAAIM scraper failed: %s", exc)
        return None


class SentimentScraper:
    """Scrape AAII and NAAIM sentiment data. Failures are logged and skipped."""

    def fetch(self) -> dict[str, pd.Series]:
        result: dict[str, pd.Series] = {}
        aaii = _fetch_aaii()
        if aaii is not None:
            result["aaii_bull_bear"] = aaii
        naaim = _fetch_naaim()
        if naaim is not None:
            result["naaim_exposure"] = naaim
        return result
