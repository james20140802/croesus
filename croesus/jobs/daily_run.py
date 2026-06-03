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
from croesus.fx.ingest_fx_rates import FxIngestionResult, ingest_fx_rates
from croesus.macro._loader import load_latest_macro_state
from croesus.macro.screening_adapter import get_screening_params, neutral_screening_params
from croesus.prices.ingest_prices import IngestionResult, ingest_daily_prices


@dataclass(frozen=True)
class DailyRunResult:
    price_result: IngestionResult
    fx_result: FxIngestionResult
    factor_result: FactorComputationResult
    screening_params: dict


def run_daily_pipeline(
    conn: duckdb.DuckDBPyConnection,
    *,
    source: DailyPriceSource | None = None,
    log: Callable[[str], None] = print,
) -> DailyRunResult:
    seed_us_equities(conn)
    price_result = ingest_daily_prices(conn, source=source, log=log)
    fx_result = ingest_fx_rates(
        conn,
        currencies=_fx_currencies_for_daily_run(conn),
        log=log,
    )
    factor_result = compute_and_store_common_factors(conn)

    # Consume the latest MacroState (from daily_macro_run) to adjust screening
    # parameters. Falls back to neutral defaults if no macro data is available.
    macro_state = load_latest_macro_state(conn)
    if macro_state is None:
        log("no MacroState found — run daily_macro_run first; using neutral params")
        screening_params = neutral_screening_params()
    else:
        screening_params = get_screening_params(macro_state)

    return DailyRunResult(
        price_result=price_result,
        fx_result=fx_result,
        factor_result=factor_result,
        screening_params=screening_params,
    )


def _fx_currencies_for_daily_run(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        """
        SELECT currency FROM assets WHERE currency IS NOT NULL
        UNION
        SELECT base_currency FROM portfolios WHERE base_currency IS NOT NULL
        UNION
        SELECT currency FROM portfolio_holdings WHERE currency IS NOT NULL
        """
    ).fetchall()
    return sorted({row[0].upper() for row in rows if row[0]})


def main() -> None:
    migrate()
    with get_connection() as conn:
        result = run_daily_pipeline(conn)
    sp = result.screening_params
    print(
        "daily run complete: "
        f"{len(result.price_result.succeeded)} price downloads succeeded, "
        f"{len(result.price_result.failed)} failed, "
        f"{len(result.factor_result.computed)} assets with factors"
    )
    print(
        "macro-adjusted screening params: "
        f"regime={sp['regime']} positioning={sp['positioning']} "
        f"candidate_count={sp['candidate_count']} weights={sp['factor_weights']}"
    )


if __name__ == "__main__":
    main()
