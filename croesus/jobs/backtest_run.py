"""CLI entry point for the backtest harness.

Usage::

    python -m croesus.jobs.backtest_run \\
        --start 2020-01-01 \\
        --end   2024-12-31 \\
        [--top-n 5] \\
        [--cost-bps 10.0] \\
        [--reports-dir reports] \\
        [--db-path storage/croesus.duckdb]

The job runs all weight schemes defined in BacktestConfig (default:
``composite_v1`` and ``momentum_only``), prints the comparison table, and
writes ``reports/backtest/<end-date>/backtest.md`` and ``backtest.csv``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from croesus.backtest.config import BacktestConfig, _default_schemes
from croesus.backtest.engine import run_backtest
from croesus.backtest.metrics import summarize
from croesus.reports.backtest import write_backtest_report


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.backtest_run",
        description="Walk-forward backtest of the screening + rebalancing rules.",
    )
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=5, help="Holdings per rebalance (default 5)")
    parser.add_argument("--cost-bps", type=float, default=10.0, help="One-way cost in bps (default 10)")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=Path("reports"),
        help="Root directory for output reports (default 'reports')",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to DuckDB file (default: storage/croesus.duckdb)",
    )
    args = parser.parse_args()

    config = BacktestConfig(
        start_date=args.start,
        end_date=args.end,
        top_n=args.top_n,
        cost_bps=args.cost_bps,
        weight_schemes=_default_schemes(),
        benchmark_symbol="SPY",
    )

    print(f"Running backtest {config.start_date} → {config.end_date}")
    print(f"  Schemes: {', '.join(config.weight_schemes)}")
    print(f"  Top-N: {config.top_n}, Cost: {config.cost_bps} bps, Benchmark: {config.benchmark_symbol}")
    print()

    results = run_backtest(config, db_path=args.db_path)

    # Print comparison table
    print(f"{'Scheme':<30} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'TotRet':>8} {'Turnover':>10}")
    print("-" * 76)

    def _pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v * 100:.2f}%"

    def _f(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.3f}"

    for scheme_name in config.weight_schemes:
        r = results.get(scheme_name)
        if r is None:
            continue
        m = summarize(r.equity_curve)
        print(
            f"{scheme_name:<30} {_pct(m['cagr']):>8} {_f(m['sharpe']):>8} "
            f"{_pct(m['max_drawdown']):>8} {_pct(m['total_return']):>8} {_f(r.total_turnover):>10}"
        )

    bench = results.get("__benchmark__")
    if bench is not None:
        m = summarize(bench.equity_curve)
        label = f"{config.benchmark_symbol} (B&H)"
        print(
            f"{label:<30} {_pct(m['cagr']):>8} {_f(m['sharpe']):>8} "
            f"{_pct(m['max_drawdown']):>8} {_pct(m['total_return']):>8} {'0.000':>10}"
        )

    print()
    md_path, csv_path = write_backtest_report(results, config, reports_dir=args.reports_dir)
    print("Report written:")
    print(f"  {md_path}")
    print(f"  {csv_path}")


if __name__ == "__main__":
    main()
