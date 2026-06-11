from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.data_sources.base import DailyPriceSource
from croesus.data_sources.yfinance_source import YFinanceDailyPriceSource
from croesus.prices.repository import PriceRepository


@dataclass(frozen=True)
class IngestionResult:
    succeeded: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def ingest_daily_prices(
    conn: duckdb.DuckDBPyConnection,
    source: DailyPriceSource | None = None,
    *,
    period: str = "1y",
    log: Callable[[str], None] = print,
) -> IngestionResult:
    source = source or YFinanceDailyPriceSource()
    repo = AssetRepository(conn)
    # Equities are the valuation universe; ETFs (e.g. the SPY benchmark) are
    # priced too so the valuation layer can regress betas against the market.
    assets = repo.list_active(asset_type="equity", country="US")
    assets += repo.list_active(asset_type="etf", country="US")
    prices = PriceRepository(conn)
    result = IngestionResult()

    for asset in assets:
        try:
            frame = source.fetch_daily_prices(asset.symbol, period=period)
            if frame.empty:
                result.skipped[asset.symbol] = "no price rows returned"
                log(f"skip {asset.symbol}: no price rows returned")
                continue
            rows = prices.upsert_daily_prices(asset.asset_id, frame, source="yfinance")
            result.succeeded.append(asset.symbol)
            log(f"stored {rows} daily price rows for {asset.symbol}")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result
