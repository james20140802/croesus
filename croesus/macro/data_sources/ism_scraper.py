"""
ISM Manufacturing and Services PMI scraper.

ISM data was removed from FRED in June 2016 (licensing dispute).
This module scrapes the ISM website directly as a best-effort replacement.
Failures are logged and skipped — CFNAI from FRED serves as the fallback.

ISM publishes one page per month (e.g. ".../ism-pmi-reports/pmi/may/"), and
that page carries a "past 12 months" composite table. Because the month in the
URL changes every release, we do NOT hard-code it: we read the report index and
discover the latest monthly report link for each series, then parse its table.

NOTE: The ISM website structure changes periodically. If this scraper stops
working, verify the index and report layout at:
  - https://www.ismworld.org/supply-management-news-and-reports/reports/ism-pmi-reports/
"""

from __future__ import annotations

import logging
import re
from io import StringIO

import pandas as pd

logger = logging.getLogger(__name__)

_BASE = "https://www.ismworld.org"
_INDEX_URL = (
    "https://www.ismworld.org/supply-management-news-and-reports/"
    "reports/ism-pmi-reports/"
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}

# Monthly report links on the index page, per series. Manufacturing lives under
# ".../pmi/<month>/" and services under ".../services/<month>/".
_REPORT_HREF = {
    "ism_mfg_pmi": re.compile(r'href="(/[^"]*?/ism-pmi-reports/pmi/[a-z]+/)"', re.IGNORECASE),
    "ism_svc_pmi": re.compile(r'href="(/[^"]*?/ism-pmi-reports/services/[a-z]+/)"', re.IGNORECASE),
}
# Guardrail: never fetch more than this many candidate report pages per series.
_MAX_CANDIDATES = 6

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
        tables = pd.read_html(StringIO(html), flavor="lxml")
    except Exception:
        try:
            tables = pd.read_html(StringIO(html))
        except Exception as exc:
            logger.debug("read_html failed for %s: %s", series_name, exc)
            return None

    for df in tables:
        if df.shape[0] < 6 or df.shape[1] < 2:
            continue

        # Try to parse dates from the first column. ISM labels months as either
        # "May 2024" (%B %Y) or "May 24" (%b %Y); fall back to inference only if
        # neither explicit format matches enough rows.
        first_col = df.iloc[:, 0]
        raw_dates = pd.to_datetime(first_col, format="%b %Y", errors="coerce")
        if raw_dates.notna().sum() < 4:
            raw_dates = pd.to_datetime(first_col, format="%B %Y", errors="coerce")
        if raw_dates.notna().sum() < 4:
            raw_dates = pd.to_datetime(first_col, format="mixed", errors="coerce")
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


def _discover_report_urls(index_html: str, series_name: str) -> list[str]:
    """Return absolute URLs of the monthly report pages for ``series_name``."""
    pattern = _REPORT_HREF[series_name]
    seen: list[str] = []
    for path in pattern.findall(index_html):
        url = _BASE + path
        if url not in seen:
            seen.append(url)
    return seen[:_MAX_CANDIDATES]


def _get(url: str) -> str | None:
    try:
        import requests

        resp = requests.get(url, timeout=20, headers=_HEADERS)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.warning("ISM fetch failed for %s: %s", url, exc)
        return None


def _fetch_latest(index_html: str, series_name: str) -> pd.Series | None:
    """
    Discover the monthly report links for ``series_name`` on the index page,
    parse each, and return the series whose table reaches the latest month.
    """
    urls = _discover_report_urls(index_html, series_name)
    if not urls:
        logger.warning("ISM %s: no monthly report link found on index page", series_name)
        return None

    best: pd.Series | None = None
    for url in urls:
        html = _get(url)
        if html is None:
            continue
        series = _parse_ism_table(html, series_name)
        if series is None or series.empty:
            continue
        if best is None or series.index.max() > best.index.max():
            best = series
    if best is None:
        logger.warning("ISM %s: report pages found but none were parseable", series_name)
    return best


class ISMScraper:
    """Scrape ISM Manufacturing and Services PMI. Failures are logged and skipped."""

    def fetch(self) -> dict[str, pd.Series]:
        result: dict[str, pd.Series] = {}

        index_html = _get(_INDEX_URL)
        if index_html is None:
            return result

        for series_name in ("ism_mfg_pmi", "ism_svc_pmi"):
            series = _fetch_latest(index_html, series_name)
            if series is not None and not series.empty:
                result[series_name] = series

        return result
