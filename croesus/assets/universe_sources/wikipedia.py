"""
Wikipedia index-constituent sources (Sprint 008c).

The S&P 500 and NASDAQ-100 list pages carry maintained constituent tables with
symbol, company name, and GICS sector/sub-industry — enough to register an
asset without one yfinance call per ticker (metadata enrichment stays lazy via
the resolver). No API key, no rate limits at a weekly cadence.

Parsing is column-name driven (``Symbol``/``Ticker`` + ``Security``/``Company``)
so the table position on the page can move without breaking the fetch.
"""
from __future__ import annotations

import urllib.request
from io import StringIO

import pandas as pd

from croesus.assets.universe_sources.base import UniverseConstituent, UniverseSource

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ100_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"

# Wikipedia rejects the default urllib agent; identify the client honestly.
_USER_AGENT = "croesus-research/0.1 (local quant research pipeline)"

_SYMBOL_COLUMNS = ("Symbol", "Ticker")
_NAME_COLUMNS = ("Security", "Company")
_SECTOR_COLUMNS = ("GICS Sector", "Sector")
_INDUSTRY_COLUMNS = ("GICS Sub-Industry", "Sub-Industry", "Industry")


class WikipediaIndexSource:
    """Fetch one index's constituents from its Wikipedia list page."""

    def __init__(self, index_name: str, url: str) -> None:
        self.index_name = index_name
        self.url = url
        self.source_name = f"wikipedia_{index_name}"

    def fetch_constituents(self) -> list[UniverseConstituent]:
        request = urllib.request.Request(self.url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8", errors="replace")
        tables = pd.read_html(StringIO(html))
        constituents = constituents_from_tables(tables, index_name=self.index_name)
        if not constituents:
            raise ValueError(
                f"no constituent table found on {self.url} "
                f"(looked for columns {_SYMBOL_COLUMNS} + {_NAME_COLUMNS})"
            )
        return constituents


def default_universe_sources() -> list[UniverseSource]:
    return [
        WikipediaIndexSource("sp500", SP500_URL),
        WikipediaIndexSource("nasdaq100", NASDAQ100_URL),
    ]


def constituents_from_tables(
    tables: list[pd.DataFrame], *, index_name: str
) -> list[UniverseConstituent]:
    """Pick the constituent table out of a page's tables and normalize rows.

    The constituent table is the one carrying both a symbol column and a
    company-name column; sector/industry columns are optional. Split into a
    pure function so parsing is testable without network access.
    """
    for table in tables:
        if not all(isinstance(c, str) for c in table.columns):
            continue  # MultiIndex headers — not the constituent table
        symbol_col = _first_present(table, _SYMBOL_COLUMNS)
        name_col = _first_present(table, _NAME_COLUMNS)
        if symbol_col is None or name_col is None:
            continue
        sector_col = _first_present(table, _SECTOR_COLUMNS)
        industry_col = _first_present(table, _INDUSTRY_COLUMNS)

        constituents: list[UniverseConstituent] = []
        for row in table.itertuples(index=False):
            values = dict(zip(table.columns, row))
            symbol = _clean(values.get(symbol_col))
            if symbol is None:
                continue
            constituents.append(
                UniverseConstituent(
                    symbol=symbol,
                    name=_clean(values.get(name_col)),
                    sector=_clean(values.get(sector_col)) if sector_col else None,
                    industry=_clean(values.get(industry_col)) if industry_col else None,
                    index_name=index_name,
                )
            )
        if constituents:
            return constituents
    return []


def _first_present(table: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    for name in candidates:
        if name in table.columns:
            return name
    return None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None
