from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import duckdb

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.data_sources.base import DailyPriceSource
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.compute_common_factors import (
    FactorComputationResult,
    compute_and_store_common_factors,
)
from croesus.prices.ingest_prices import IngestionResult, ingest_daily_prices


@dataclass(frozen=True)
class DailyRunResult:
    price_result: IngestionResult
    factor_result: FactorComputationResult


def run_daily_pipeline(
    conn: duckdb.DuckDBPyConnection,
    *,
    source: DailyPriceSource | None = None,
    log: Callable[[str], None] = print,
) -> DailyRunResult:
    seed_us_equities(conn)
    price_result = ingest_daily_prices(conn, source=source, log=log)
    factor_result = compute_and_store_common_factors(conn)
    return DailyRunResult(price_result=price_result, factor_result=factor_result)


def main() -> None:
    migrate()
    with get_connection() as conn:
        result = run_daily_pipeline(conn)
    print(
        "daily run complete: "
        f"{len(result.price_result.succeeded)} price downloads succeeded, "
        f"{len(result.price_result.failed)} failed, "
        f"{len(result.factor_result.computed)} assets with factors"
    )


if __name__ == "__main__":
    main()
