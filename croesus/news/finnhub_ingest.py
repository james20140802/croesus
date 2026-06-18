from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.news.models import SOURCE_FINNHUB, NewsIngestionResult
from croesus.news.repository import NewsRepository
from croesus.news.source import FinnhubNewsSource, NewsSource

FILER_ASSET_TYPES = ("equity",)
DEFAULT_LOOKBACK_DAYS = 7


def ingest_finnhub_news(
    conn: duckdb.DuckDBPyConnection,
    source: NewsSource | None = None,
    *,
    as_of: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    log: Callable[[str], None] = print,
) -> NewsIngestionResult:
    """Fetch recent Finnhub company news for every active equity and persist it.

    Per-symbol failures are isolated so one rate-limited ticker never stops the
    run. ``as_of`` defaults to today; news from the prior ``lookback_days`` is
    requested (idempotent upsert dedupes overlap with earlier runs).
    """
    source = source or FinnhubNewsSource()
    as_of = as_of or date.today()
    since = as_of - timedelta(days=lookback_days)

    # Equity filers only. Finnhub's /company-news returns [] for tickers outside
    # its covered (mostly US) exchanges, so non-US equities simply yield no news
    # rather than erroring — consistent with the disclosures ingest filter.
    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES
    ]
    # Key by UPPER symbol: parse_company_news upper-cases all tickers, so the
    # link lookup must too (else a non-uppercase asset symbol would never match).
    symbol_to_asset = {a.symbol.upper(): a.asset_id for a in assets}
    repo = NewsRepository(conn)
    result = NewsIngestionResult()

    for asset in assets:
        try:
            articles = source.fetch_company_news(asset.symbol, since=since, until=as_of)
            stored = repo.upsert_articles(
                SOURCE_FINNHUB, articles, symbol_to_asset=symbol_to_asset
            )
            result.scanned.append(asset.symbol)
            result.stored += stored
            if stored:
                log(f"{asset.symbol}: {stored} article(s)")
        except Exception as exc:  # noqa: BLE001 - per-symbol failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result
