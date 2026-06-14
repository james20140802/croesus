"""
Quarterly valuation refresh (Sprint 007).

Financial statements update on a quarterly cadence, so the expensive absolute
valuation runs here rather than in daily_run:

  seed assets
    -> ingest fundamentals (yfinance statements -> fundamentals table)
    -> recompute DCF + multiples + sector percentiles (include_dcf=True)
       -> factor_values (incl. price_to_intrinsic) + valuation_snapshots

Per-asset failures are skipped and logged. This job computes and records; it
never trades.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence

import duckdb

from croesus.assets.seed_benchmarks import seed_benchmarks
from croesus.assets.seed_us_equities import seed_us_equities
from croesus.data_sources.fundamentals.base import FundamentalsProvider
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.equity.compute_quality import compute_and_store_quality_factors
from croesus.factors.equity.compute_valuation import (
    ValuationComputationResult,
    compute_and_store_valuation_factors,
)
from croesus.fundamentals.ingest_fundamentals import (
    FundamentalsIngestionResult,
    ingest_fundamentals,
)


@dataclass(frozen=True)
class QuarterlyRunResult:
    fundamentals_result: FundamentalsIngestionResult
    valuation_result: ValuationComputationResult


def run_quarterly_pipeline(
    conn: duckdb.DuckDBPyConnection,
    *,
    provider: FundamentalsProvider | None = None,
    as_of: date | None = None,
    log: Callable[[str], None] = print,
) -> QuarterlyRunResult:
    """Ingest fundamentals, then recompute valuation factors including the DCF.

    Expects an already-migrated connection.
    """
    seed_us_equities(conn)
    seed_benchmarks(conn)
    fundamentals_result = ingest_fundamentals(conn, provider, log=log)
    valuation_result = compute_and_store_valuation_factors(
        conn, include_dcf=True, as_of=as_of, log=log
    )
    compute_and_store_quality_factors(conn, as_of=as_of, log=log)
    return QuarterlyRunResult(
        fundamentals_result=fundamentals_result,
        valuation_result=valuation_result,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.quarterly_run",
        description=(
            "Ingest quarterly fundamentals and recompute the valuation layer "
            "(multiples, sector percentiles, and the 2-stage DCF). Computation "
            "and recording only — never trades."
        ),
    )
    parser.add_argument(
        "--date",
        dest="as_of_date",
        metavar="YYYY-MM-DD",
        help="valuation as-of date (default: today)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    as_of = date.fromisoformat(args.as_of_date) if args.as_of_date else None

    migrate()
    with get_connection() as conn:
        result = run_quarterly_pipeline(conn, as_of=as_of)
    fr = result.fundamentals_result
    vr = result.valuation_result
    print(
        "quarterly run complete: "
        f"{len(fr.succeeded)} symbols with fundamentals, "
        f"{len(fr.failed)} failed, "
        f"{len(vr.dcf_computed)} DCF valuations, "
        f"{len(vr.dcf_skipped)} DCF skipped"
    )


if __name__ == "__main__":
    main()
