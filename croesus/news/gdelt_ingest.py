from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.news.body_fetch import ArticleBodyFetcher, TrafilaturaBodyFetcher
from croesus.news.gdelt_parse import company_query_term
from croesus.news.gdelt_source import GdeltDocSource, GdeltNewsSource
from croesus.news.models import SOURCE_GDELT, NewsIngestionResult
from croesus.news.repository import NewsRepository

FILER_ASSET_TYPES = ("equity",)
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_LIMIT_PER_ASSET = 5


def ingest_gdelt_news(
    conn: duckdb.DuckDBPyConnection,
    source: GdeltNewsSource | None = None,
    body_fetcher: ArticleBodyFetcher | None = None,
    *,
    as_of: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit_per_asset: int = DEFAULT_LIMIT_PER_ASSET,
    log: Callable[[str], None] = print,
) -> NewsIngestionResult:
    """Discover broad GDELT news per active equity (by company name), fetch each
    article's body, and persist it (``source='gdelt'``, ``relation='queried'``).

    Per-asset failures are isolated. Assets with no usable name are skipped.
    """
    source = source or GdeltDocSource()
    body_fetcher = body_fetcher or TrafilaturaBodyFetcher()
    as_of = as_of or date.today()
    since = as_of - timedelta(days=lookback_days)

    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES
    ]
    repo = NewsRepository(conn)
    result = NewsIngestionResult()

    for asset in assets:
        query_term = company_query_term(asset.name)
        if not query_term:
            # No usable company name to query GDELT with — record it so a universe
            # full of empty names can't masquerade as a healthy scanned=0 run.
            result.skipped.append(asset.symbol)
            continue
        symbol = asset.symbol.upper()
        try:
            articles = source.fetch_articles(query_term, since=since, until=as_of)
            enriched = [
                replace(
                    art,
                    tickers=(symbol,),
                    body=(body_fetcher.fetch_body(art.url) if art.url else None),
                )
                for art in articles[:limit_per_asset]
            ]
            stored = repo.upsert_articles(
                SOURCE_GDELT,
                enriched,
                symbol_to_asset={symbol: asset.asset_id},
            )
            result.scanned.append(asset.symbol)
            result.stored += stored
            if stored:
                log(f"{asset.symbol}: {stored} GDELT article(s)")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result
