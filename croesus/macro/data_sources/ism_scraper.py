from __future__ import annotations

"""
ISM Manufacturing and Services PMI scraper.

ISM data was removed from FRED in June 2016 (licensing dispute).
This module scrapes the ISM website directly as a best-effort replacement.
Failures are logged and skipped — CFNAI from FRED serves as the fallback.

NOTE: The ISM website structure changes periodically. If this scraper stops
working, verify the table format at:
  - https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/pmi/
  - https://www.ismworld.org/supply-management-news-and-reports/reports/ism-report-on-business/services/
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_MFG_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "reports/ism-report-on-business/pmi/"
)
_SVC_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "reports/ism-report-on-business/services/"
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}

# PMI composite values are always in this range
_PMI_MIN, _PMI_MAX = 25.0, 80.0
_PMI_STD_MAX = 12.0  # reject columns with implausible variance


def _parse_ism_table(html: str, series_name: str) -> pd.Series | None:
    """
    Extract the PMI composite time series from ISM page HTML.

    ISM tables have a date-like first column (e.g. "May 2024") and the
    composite PMI index as the first numeric column.
    Returns a pd.Series indexed by datetime, or None if parsing fails.
    """
    try:
        tables = pd.read_html(html, flavor="lxml")
    except Exception:
        try:
            tables = pd.read_html(html)
        except Exception as exc:
            logger.debug("read_html failed for %s: %s", series_name, exc)
            return None

    for df in tables:
        if df.shape[0] < 6 or df.shape[1] < 2:
            continue

        # Try to parse dates from the first column
        raw_dates = pd.to_datetime(df.iloc[:, 0], format="%b %Y", errors="coerce")
        if raw_dates.notna().sum() < 4:
            raw_dates = pd.to_datetime(df.iloc[:, 0], errors="coerce")
        if raw_dates.notna().sum() < 4:
            continue

        # Find the first numeric column whose values look like a PMI composite
        for col in df.columns[1:]:
            vals = pd.to_numeric(df[col], errors="coerce")
            valid = vals.dropna()
            if (
                len(valid) >= 6
                and _PMI_MIN <= float(valid.mean()) <= _PMI_MAX
                and float(valid.std()) < _PMI_STD_MAX
            ):
                s = pd.Series(vals.values, index=raw_dates, name=series_name)
                s = s.dropna().sort_index()
                logger.debug(
                    "ISM %s: parsed %d observations (latest=%.1f)",
                    series_name,
                    len(s),
                    float(s.iloc[-1]),
                )
                return s

    return None


def _fetch_ism(url: str, series_name: str) -> pd.Series | None:
    try:
        import requests

        resp = requests.get(url, timeout=20, headers=_HEADERS)
        resp.raise_for_status()
        return _parse_ism_table(resp.text, series_name)
    except Exception as exc:
        logger.warning("ISM %s scraper failed: %s", series_name, exc)
        return None


class ISMScraper:
    """Scrape ISM Manufacturing and Services PMI. Failures are logged and skipped."""

    def fetch(self) -> dict[str, pd.Series]:
        result: dict[str, pd.Series] = {}

        mfg = _fetch_ism(_MFG_URL, "ism_mfg_pmi")
        if mfg is not None:
            result["ism_mfg_pmi"] = mfg

        svc = _fetch_ism(_SVC_URL, "ism_svc_pmi")
        if svc is not None:
            result["ism_svc_pmi"] = svc

        return result
