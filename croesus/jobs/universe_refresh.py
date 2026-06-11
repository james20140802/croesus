"""
Weekly screening-universe refresh (Sprint 008c).

Registers/refreshes S&P 500 + NASDAQ-100 constituents in the asset registry so
screening runs against a real-scale universe instead of the seed tickers. New
names get their first price history from the next ``daily_run`` (price
ingestion always fetches a 1y window), which is enough for every common factor.

Failure policy follows the Sprint 008a integrity contract: a single failing
index degrades loudly (warn-level ``data_quality_issues`` row + summary note,
the other index still lands); *all* sources failing raises, so the sync run
records a failure and the ``asset_universe`` domain stays due for retry.
"""
from __future__ import annotations

import argparse
from typing import Sequence

import duckdb

from croesus.assets.ingest_universe import UniverseIngestionResult, ingest_universe
from croesus.assets.universe_sources.base import UniverseSource
from croesus.assets.universe_sources.wikipedia import default_universe_sources
from croesus.db.connection import get_connection, resolve_db_path
from croesus.db.migrate import migrate
from croesus.quality.models import (
    CODE_UNIVERSE_SOURCE_FAILED,
    SEVERITY_WARN,
    DataQualityIssue,
)
from croesus.quality.repository import DataQualityRepository


class UniverseRefreshError(RuntimeError):
    """Every universe source failed — nothing was refreshed."""


def run_universe_refresh(
    conn: duckdb.DuckDBPyConnection,
    sources: list[UniverseSource] | None = None,
) -> UniverseIngestionResult:
    sources = sources if sources is not None else default_universe_sources()
    result = ingest_universe(conn, sources)

    if result.failed_sources and not result.fetched:
        detail = "; ".join(f"{name}: {err}" for name, err in result.failed_sources.items())
        raise UniverseRefreshError(f"all universe sources failed — {detail}")

    if result.failed_sources:
        DataQualityRepository(conn).record_many(
            [
                DataQualityIssue(
                    domain="asset_universe",
                    severity=SEVERITY_WARN,
                    code=CODE_UNIVERSE_SOURCE_FAILED,
                    message=f"universe source {name} failed: {error}",
                )
                for name, error in result.failed_sources.items()
            ]
        )

    return result


def summarize(result: UniverseIngestionResult) -> str:
    parts = [
        f"constituents={result.total_constituents}",
        f"added={result.added}",
        f"updated={result.updated}",
        f"unchanged={result.unchanged}",
    ]
    if result.failed_sources:
        parts.append(f"failed_sources={','.join(sorted(result.failed_sources))}")
    if result.skipped_symbols:
        parts.append(f"skipped_symbols={len(result.skipped_symbols)}")
    return " ".join(parts)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.universe_refresh",
        description="Refresh the S&P 500 + NASDAQ-100 screening universe.",
    )
    parser.add_argument("--db-path", default=None, help="override the DuckDB path")
    args = parser.parse_args(argv)

    resolved = resolve_db_path(args.db_path)
    migrate(resolved)
    with get_connection(resolved) as conn:
        result = run_universe_refresh(conn)

    print(f"universe refresh: {summarize(result)}")
    for name, error in result.failed_sources.items():
        print(f"  WARNING {name}: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
