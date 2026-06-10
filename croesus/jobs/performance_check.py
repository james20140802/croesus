"""
Performance and goal-tracking job (Sprint 006d).

``run_performance_check`` gathers the inputs the return math needs — snapshot
history, the transaction ledger, concentration exposures, and policy drift — and
produces one progress row per period: contribution-adjusted return, the gap to
the profile's ``expected_annual_return``, a risk status shown beside it, and a
lightweight attribution. It persists the rows and returns an app-ready
:class:`PerformanceCheckResult` for a dashboard or report.

This job only *reports*. It never trades, never places an order, and never
mutates holdings or the ledger.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from typing import Any, Callable, Sequence

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.holdings_from_transactions import (
    derive_holdings_from_transactions,
)
from croesus.portfolio.performance import (
    DEFAULT_PERIODS,
    PERIOD_SINCE_INCEPTION,
    PerformanceCheckResult,
    PerformancePeriod,
    build_performance_period,
    max_drawdown,
    period_start_date,
)
from croesus.portfolio.performance_repository import PerformanceRepository
from croesus.portfolio.repository import PortfolioRepository
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import (
    TXN_DEPOSIT,
    TXN_WITHDRAWAL,
    PortfolioTransaction,
)
from croesus.profiles.models import InvestorProfile
from croesus.profiles.repository import ProfileRepository

_DEFAULT_PORTFOLIO_ID = "default"


def run_performance_check(
    conn: duckdb.DuckDBPyConnection,
    *,
    portfolio_id: str = _DEFAULT_PORTFOLIO_ID,
    as_of_date: date | None = None,
    periods: list[str] | None = None,
    log: Callable[[str], None] = print,
) -> PerformanceCheckResult:
    """Compute and persist goal-progress rows for ``portfolio_id``.

    Expects an already-migrated connection. Reads snapshots/transactions/
    exposures/drifts; writes ``portfolio_performance_snapshots`` rows. Periods
    with no usable start or end snapshot are reported as ``insufficient_history``
    rather than with a fabricated return.
    """
    as_of = as_of_date or date.today()
    period_names = list(periods) if periods else list(DEFAULT_PERIODS)
    warnings: list[str] = []

    profile = _resolve_profile(conn, portfolio_id)
    base_currency = profile.base_currency.value if profile else "USD"
    target_return_pct = profile.expected_annual_return if profile else None
    max_tolerable_drawdown = profile.max_tolerable_drawdown if profile else None

    perf_repo = PerformanceRepository(conn)
    portfolio_repo = PortfolioRepository(conn)
    txns = TransactionRepository(conn).list_transactions(portfolio_id)
    history = perf_repo.get_snapshot_history(portfolio_id, up_to=as_of)

    end_point = _value_at(history, as_of)
    has_risk_data = end_point is not None
    inception_date = history[0]["as_of_date"] if history else None

    # Concentration violations and policy drift are point-in-time at the latest
    # snapshot, so they are the same for every period.
    n_violations = 0
    n_drift_outside = 0
    if end_point is not None:
        end_date = end_point[0]
        n_violations = sum(
            1 for e in portfolio_repo.get_exposures(portfolio_id, end_date) if e.is_violation
        )
        n_drift_outside = sum(
            1 for d in portfolio_repo.get_drifts(portfolio_id, end_date) if d.is_outside_band
        )

    if end_point is None:
        warnings.append(
            f"no portfolio snapshot on or before {as_of}; "
            "run portfolio_snapshot first for a return number"
        )

    rows: list[PerformancePeriod] = []
    for name in period_names:
        row = _build_period(
            name,
            portfolio_id=portfolio_id,
            as_of=as_of,
            history=history,
            end_point=end_point,
            inception_date=inception_date,
            txns=txns,
            base_currency=base_currency,
            target_return_pct=target_return_pct,
            max_tolerable_drawdown=max_tolerable_drawdown,
            n_violations=n_violations,
            n_drift_outside=n_drift_outside,
            has_risk_data=has_risk_data,
            warnings=warnings,
        )
        rows.append(row)

    perf_repo.save_periods(rows)

    result = PerformanceCheckResult(
        portfolio_id=portfolio_id,
        as_of_date=as_of,
        periods=rows,
        warnings=warnings,
    )
    _log_summary(result, profile, log)
    return result


def _build_period(
    name: str,
    *,
    portfolio_id: str,
    as_of: date,
    history: list[dict[str, Any]],
    end_point: tuple[date, float] | None,
    inception_date: date | None,
    txns: list[PortfolioTransaction],
    base_currency: str,
    target_return_pct: float | None,
    max_tolerable_drawdown: float | None,
    n_violations: int,
    n_drift_outside: int,
    has_risk_data: bool,
    warnings: list[str],
) -> PerformancePeriod:
    end_value = end_point[1] if end_point else None
    notes: list[str] = []

    if name == PERIOD_SINCE_INCEPTION:
        # Inception starts before the first dollar: start_value is 0 and every
        # contribution counts. The annualization span runs from the first
        # snapshot date.
        start_value: float | None = 0.0 if end_point is not None else None
        span_start = inception_date
        window_start: date | None = None
        if inception_date is not None:
            notes.append(f"since first snapshot on {inception_date}")
    else:
        boundary = period_start_date(as_of, name)
        start_point = _value_at(history, boundary) if boundary else None
        if start_point is None:
            start_value = None
            span_start = None
            window_start = boundary
        else:
            span_start = start_point[0]
            start_value = start_point[1]
            window_start = start_point[0]

    net_contributions = _net_contributions(
        txns,
        start_exclusive=window_start,
        end_inclusive=as_of,
        base_currency=base_currency,
        warnings=warnings,
    )
    realized, dividends = _window_realized_dividends(
        txns,
        portfolio_id=portfolio_id,
        start_date=window_start,
        end_date=as_of,
        base_currency=base_currency,
    )
    drawdown = _window_drawdown(history, start=window_start, end=as_of)

    return build_performance_period(
        portfolio_id=portfolio_id,
        as_of_date=as_of,
        period=name,
        start_date=span_start,
        start_value=start_value,
        end_value=end_value,
        net_contributions=net_contributions,
        realized=realized,
        dividends=dividends,
        target_return_pct=target_return_pct,
        max_drawdown_pct=drawdown,
        n_violations=n_violations,
        n_drift_outside=n_drift_outside,
        max_tolerable_drawdown=max_tolerable_drawdown,
        has_risk_data=has_risk_data,
        extra_notes=notes,
    )


def _value_at(
    history: list[dict[str, Any]], target: date
) -> tuple[date, float] | None:
    """Latest snapshot ``(as_of_date, total_market_value)`` at or before ``target``."""
    chosen: tuple[date, float] | None = None
    for row in history:  # history is oldest-first
        if row["as_of_date"] > target:
            break
        value = row.get("total_market_value")
        if value is not None:
            # A NULL-valued snapshot is no data, not a $0 portfolio: skip it so
            # it neither becomes a false 0.0 end-point nor masks an earlier
            # valued snapshot. A real 0.0 (empty book) is kept.
            chosen = (row["as_of_date"], value)
    return chosen


def _net_contributions(
    txns: list[PortfolioTransaction],
    *,
    start_exclusive: date | None,
    end_inclusive: date,
    base_currency: str,
    warnings: list[str],
) -> float:
    """Deposits minus withdrawals in ``(start_exclusive, end_inclusive]``.

    Dividends, fees, buys, and sells are *internal* and are not contributions.
    Amounts are summed in their stated currency; a non-base currency flow is
    counted at face value and flagged (a first-pass, non-FX-adjusted figure).
    """
    total = 0.0
    seen_currencies: set[str] = set()
    for txn in txns:
        if txn.transaction_type not in (TXN_DEPOSIT, TXN_WITHDRAWAL):
            continue
        if start_exclusive is not None and txn.transaction_date <= start_exclusive:
            continue
        if txn.transaction_date > end_inclusive:
            continue
        amount = txn.gross_amount or 0.0
        currency = (txn.currency or base_currency).upper()
        seen_currencies.add(currency)
        if txn.transaction_type == TXN_DEPOSIT:
            total += amount
        else:
            total -= amount
    foreign = seen_currencies - {base_currency.upper()}
    if foreign:
        warnings.append(
            "net contributions mix currencies "
            f"{sorted(foreign)} with base {base_currency}; "
            "counted at face value (no FX adjustment)"
        )
    return total


def _window_realized_dividends(
    txns: list[PortfolioTransaction],
    *,
    portfolio_id: str,
    start_date: date | None,
    end_date: date,
    base_currency: str,
) -> tuple[float, float]:
    """Realized P&L and dividend income within the window.

    Computed as the difference between the cumulative figures derived at the end
    and at the start boundary, reusing the ledger's average-cost fold so the same
    accounting is applied everywhere. ``start_date`` of ``None`` (inception)
    counts everything up to ``end_date``.
    """
    end = derive_holdings_from_transactions(
        txns, portfolio_id=portfolio_id, as_of_date=end_date, base_currency=base_currency
    )
    if start_date is None:
        return end.realized_pnl, end.dividend_income
    start = derive_holdings_from_transactions(
        txns, portfolio_id=portfolio_id, as_of_date=start_date, base_currency=base_currency
    )
    return (
        end.realized_pnl - start.realized_pnl,
        end.dividend_income - start.dividend_income,
    )


def _window_drawdown(
    history: list[dict[str, Any]], *, start: date | None, end: date
) -> float | None:
    values = [
        row["total_market_value"]
        for row in history
        if (start is None or row["as_of_date"] >= start)
        and row["as_of_date"] <= end
        and row.get("total_market_value") is not None
    ]
    return max_drawdown(values)


def _resolve_profile(
    conn: duckdb.DuckDBPyConnection, portfolio_id: str
) -> InvestorProfile | None:
    """Prefer the portfolio's profile, then ``default``, then any profile."""
    profile_repo = ProfileRepository(conn)
    portfolio = PortfolioRepository(conn).get_portfolio(portfolio_id)
    if portfolio is not None:
        profile = profile_repo.get_profile(portfolio.profile_id)
        if profile is not None:
            return profile
    profile = profile_repo.get_profile(_DEFAULT_PORTFOLIO_ID)
    if profile is not None:
        return profile
    row = conn.execute(
        "SELECT profile_id FROM investor_profiles ORDER BY profile_id LIMIT 1"
    ).fetchone()
    return profile_repo.get_profile(row[0]) if row else None


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _log_summary(
    result: PerformanceCheckResult,
    profile: InvestorProfile | None,
    log: Callable[[str], None],
) -> None:
    target = profile.expected_annual_return if profile else None
    log(
        f"performance for {result.portfolio_id} @ {result.as_of_date} "
        f"(target annual return {_fmt_pct(target)}):"
    )
    for period in result.periods:
        log(
            f"  {period.period}: goal={period.status} risk={period.risk_status} "
            f"return={_fmt_pct(period.investment_return_pct)} "
            f"annualized={_fmt_pct(period.annualized_return_pct)} "
            f"gap={_fmt_pct(period.return_gap_pct)} "
            f"drawdown={_fmt_pct(period.max_drawdown_pct)}"
        )
    for warning in result.warnings:
        log(f"warning: {warning}")
    log(result.disclaimer)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.performance_check",
        description=(
            "Report whether a portfolio is on track for its profile's target "
            "return, with a risk status shown beside the return. Reporting only "
            "— never trades."
        ),
    )
    parser.add_argument(
        "--portfolio-id",
        default=_DEFAULT_PORTFOLIO_ID,
        help="portfolio to check (default: %(default)s)",
    )
    parser.add_argument(
        "--date",
        dest="as_of_date",
        metavar="YYYY-MM-DD",
        help="as-of date (default: today)",
    )
    parser.add_argument(
        "--period",
        dest="periods",
        action="append",
        metavar="PERIOD",
        help="restrict to a period (repeatable; default: all standard periods)",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="also write Markdown and CSV reports under reports/",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    as_of = None
    if args.as_of_date:
        try:
            as_of = date.fromisoformat(args.as_of_date)
        except ValueError as exc:
            print(f"invalid --date: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    migrate()
    with get_connection() as conn:
        result = run_performance_check(
            conn,
            portfolio_id=args.portfolio_id,
            as_of_date=as_of,
            periods=args.periods,
        )
        if args.report:
            from croesus.reports.performance import write_performance_reports

            md_path, csv_path = write_performance_reports(result)
            print(f"wrote {md_path}")
            print(f"wrote {csv_path}")


if __name__ == "__main__":
    main()
