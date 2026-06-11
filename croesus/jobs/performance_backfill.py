"""
Historical snapshot backfill from the transaction ledger (Sprint 009).

A portfolio recorded via transactions has exactly one live snapshot until the
daily job has run for a month — so every performance period reads
``insufficient_history``. This job reconstructs past snapshots
deterministically: for each trading day between the first transaction and the
end date, holdings are derived from the ledger (the same fold the live
snapshot uses) and marked to market with that day's stored prices and FX.

Idempotent and non-destructive: days that already have a snapshot row — live
or previously backfilled — are left untouched, so re-running is a no-op and a
backfill can never overwrite a richer live snapshot. Only the
``portfolio_snapshots`` row is written (what performance tracking reads);
per-day holdings/exposures/drifts are not reconstructed.

Days where a price or FX rate is missing still get a snapshot, but the
ERROR-level issues are recorded in ``data_quality_issues`` and the day is
counted as degraded — loud, per the Sprint 008a integrity contract.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Sequence

import duckdb

from croesus.db.connection import get_connection, resolve_db_path
from croesus.db.migrate import migrate
from croesus.jobs.portfolio_snapshot import (
    load_asset_attrs,
    load_fx_rates,
    required_currencies,
    resolve_profile,
)
from croesus.portfolio.holdings_from_transactions import (
    derive_holdings_from_transactions,
)
from croesus.portfolio.mark_to_market import mark_to_market
from croesus.portfolio.models import is_cash
from croesus.portfolio.repository import PortfolioRepository
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.prices.repository import PriceRepository
from croesus.profiles.repository import ProfileRepository
from croesus.quality.models import SEVERITY_ERROR
from croesus.quality.repository import DataQualityRepository

_DEFAULT_PORTFOLIO_ID = "default"

# ``metadata.source`` marker distinguishing reconstructed rows from live ones.
BACKFILL_SOURCE = "performance_backfill"


@dataclass(frozen=True)
class PerformanceBackfillResult:
    portfolio_id: str
    start_date: date
    end_date: date
    snapshots_written: int
    skipped_existing: int
    days_degraded: int
    warnings: list[str] = field(default_factory=list)


def run_performance_backfill(
    conn: duckdb.DuckDBPyConnection,
    *,
    portfolio_id: str = _DEFAULT_PORTFOLIO_ID,
    start_date: date | None = None,
    end_date: date | None = None,
    log: Callable[[str], None] = print,
) -> PerformanceBackfillResult:
    """Reconstruct daily snapshots for ``portfolio_id`` over the date range.

    Defaults to the first transaction date through today. Expects an
    already-migrated connection. Raises ``ValueError`` when the portfolio has
    no transactions (there is nothing to derive holdings from).
    """
    transactions = TransactionRepository(conn).list_transactions(portfolio_id)
    if not transactions:
        raise ValueError(
            f"portfolio {portfolio_id!r} has no recorded transactions — "
            "backfill derives holdings from the ledger, so record transactions first"
        )

    profile = resolve_profile(
        conn, PortfolioRepository(conn), ProfileRepository(conn), portfolio_id
    )
    base_currency = profile.base_currency.value if profile else "USD"

    start = start_date or transactions[0].transaction_date
    end = end_date or date.today()
    if end < start:
        raise ValueError(f"end date {end} precedes start date {start}")

    days = _trading_days(conn, start, end)
    existing = {
        row[0]
        for row in conn.execute(
            """
            SELECT as_of_date FROM portfolio_snapshots
            WHERE portfolio_id = ? AND as_of_date BETWEEN ? AND ?
            """,
            [portfolio_id, start, end],
        ).fetchall()
    }

    price_repo = PriceRepository(conn)
    portfolio_repo = PortfolioRepository(conn)
    quality_repo = DataQualityRepository(conn)

    written = skipped_existing = degraded = 0
    warnings: list[str] = []
    for day in days:
        if day in existing:
            skipped_existing += 1
            continue

        derived = derive_holdings_from_transactions(
            transactions,
            portfolio_id=portfolio_id,
            as_of_date=day,
            base_currency=base_currency,
        )
        if not derived.holdings:
            continue  # before the first position or balance existed

        assets_by_id = load_asset_attrs(conn, [h.asset_id for h in derived.holdings])
        fx_rates = load_fx_rates(
            conn, required_currencies(derived.holdings, base_currency), day
        )
        mark = mark_to_market(
            derived.holdings,
            price_lookup=lambda asset_id, _day=day: price_repo.get_latest_close(
                asset_id, _day
            ),
            fx_rates=fx_rates,
            assets_by_id=assets_by_id,
            base_currency=base_currency,
            as_of_date=day,
        )

        errors = [i for i in mark.issues if i.severity == SEVERITY_ERROR]
        if errors:
            degraded += 1
            quality_repo.record_many(
                errors, run_id=f"backfill-{portfolio_id}-{day.isoformat()}"
            )

        cash_value = sum(
            (h.market_value or 0.0) for h in mark.holdings if is_cash(h.asset_id)
        )
        portfolio_repo.save_snapshot(
            portfolio_id,
            day,
            mark.total_market_value,
            total_cost_basis=mark.total_cost_basis,
            unrealized_pnl=mark.unrealized_pnl,
            cash_value=cash_value,
            metadata={"source": BACKFILL_SOURCE},
        )
        written += 1

    if degraded:
        warnings.append(
            f"{degraded} backfilled day(s) are DEGRADED (missing price or FX); "
            "see data_quality_issues"
        )

    result = PerformanceBackfillResult(
        portfolio_id=portfolio_id,
        start_date=start,
        end_date=end,
        snapshots_written=written,
        skipped_existing=skipped_existing,
        days_degraded=degraded,
        warnings=warnings,
    )
    log(
        f"backfill {portfolio_id} {start}..{end}: wrote {written} snapshot(s), "
        f"kept {skipped_existing} existing, degraded {degraded}"
    )
    return result


def _trading_days(
    conn: duckdb.DuckDBPyConnection, start: date, end: date
) -> list[date]:
    """Days to reconstruct: dates with any stored price, weekdays as fallback.

    Price-table dates naturally yield exchange trading days once the universe
    is populated. The weekday fallback covers cash-only books on an empty
    price store (their value still moves with deposits/withdrawals).
    """
    rows = conn.execute(
        "SELECT DISTINCT date FROM prices_daily WHERE date BETWEEN ? AND ? ORDER BY date",
        [start, end],
    ).fetchall()
    if rows:
        return [row[0] for row in rows]

    days: list[date] = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.performance_backfill",
        description=(
            "Reconstruct historical portfolio snapshots from the transaction "
            "ledger and stored price/FX history."
        ),
    )
    parser.add_argument("--portfolio-id", default=_DEFAULT_PORTFOLIO_ID)
    parser.add_argument("--start", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--end", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--db-path", default=None, help="override the DuckDB path")
    args = parser.parse_args(argv)

    try:
        start = date.fromisoformat(args.start) if args.start else None
        end = date.fromisoformat(args.end) if args.end else None
    except ValueError as exc:
        print(f"invalid date: {exc}", file=sys.stderr)
        return 1

    resolved = resolve_db_path(args.db_path)
    migrate(resolved)
    with get_connection(resolved) as conn:
        try:
            result = run_performance_backfill(
                conn, portfolio_id=args.portfolio_id, start_date=start, end_date=end
            )
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1

    for warning in result.warnings:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
