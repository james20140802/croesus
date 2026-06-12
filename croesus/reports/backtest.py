"""Backtest report writer.

Produces a Markdown comparison table and a CSV of per-scheme metrics under
``reports/backtest/<end-date>/``.  The Markdown file always includes an
**Honest Limitations** section (hard requirement from Sprint 014 spec).
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

from croesus.backtest.config import BacktestConfig
from croesus.backtest.engine import SchemeResult
from croesus.backtest.metrics import summarize
from croesus.reports.paths import report_output_dir


def write_backtest_report(
    results: dict[str, SchemeResult],
    config: BacktestConfig,
    *,
    reports_dir: str | Path = "reports",
) -> tuple[Path, Path]:
    """Write Markdown and CSV backtest reports.

    Parameters
    ----------
    results:
        Output of ``run_backtest``.  The ``'__benchmark__'`` key, if present,
        is rendered as the benchmark row.
    config:
        The config used to produce *results* (for header metadata).
    reports_dir:
        Root directory for reports (e.g. ``"reports"``).

    Returns
    -------
    (markdown_path, csv_path)
    """
    end = date.fromisoformat(config.end_date)
    output_dir = report_output_dir(reports_dir, "backtest", end)
    md_path = output_dir / "backtest.md"
    csv_path = output_dir / "backtest.csv"

    rows = _build_metric_rows(results, config)
    md_path.write_text(_render_markdown(rows, config), encoding="utf-8")
    _write_csv(csv_path, rows)
    return md_path, csv_path


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_metric_rows(
    results: dict[str, SchemeResult],
    config: BacktestConfig,
) -> list[dict[str, Any]]:
    """Build one metrics dict per scheme, with the benchmark last."""
    rows: list[dict[str, Any]] = []

    benchmark = results.get("__benchmark__")

    for scheme_name in config.weight_schemes:
        result = results.get(scheme_name)
        if result is None:
            continue
        m = summarize(result.equity_curve)
        rows.append({
            "scheme": scheme_name,
            "cagr": m["cagr"],
            "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"],
            "total_return": m["total_return"],
            "turnover": result.total_turnover,
            "is_benchmark": False,
        })

    if benchmark is not None:
        m = summarize(benchmark.equity_curve)
        rows.append({
            "scheme": config.benchmark_symbol + " (buy-and-hold)",
            "cagr": m["cagr"],
            "sharpe": m["sharpe"],
            "max_drawdown": m["max_drawdown"],
            "total_return": m["total_return"],
            "turnover": 0.0,
            "is_benchmark": True,
        })

    return rows


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:.2f}%"


def _fmt_float(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{decimals}f}"


def _render_markdown(
    rows: list[dict[str, Any]],
    config: BacktestConfig,
) -> str:
    lines: list[str] = []
    lines.append(f"# Backtest Report — {config.start_date} to {config.end_date}")
    lines.append("")
    lines.append(
        f"**Universe**: equity + ETF assets in DB  "
        f"| **Top-N**: {config.top_n}  "
        f"| **Rebalance**: {config.rebalance_frequency}  "
        f"| **Cost**: {config.cost_bps} bps one-way  "
        f"| **Benchmark**: {config.benchmark_symbol}"
    )
    lines.append("")

    # Comparison table
    lines.append("## Scheme Comparison")
    lines.append("")
    header = "| Scheme | CAGR | Sharpe | MDD | Total Return | Turnover |"
    sep = "|---|---|---|---|---|---|"
    lines.append(header)
    lines.append(sep)
    for row in rows:
        marker = " *(benchmark)*" if row["is_benchmark"] else ""
        lines.append(
            f"| {row['scheme']}{marker} "
            f"| {_fmt_pct(row['cagr'])} "
            f"| {_fmt_float(row['sharpe'])} "
            f"| {_fmt_pct(row['max_drawdown'])} "
            f"| {_fmt_pct(row['total_return'])} "
            f"| {_fmt_float(row['turnover'], 3)} |"
        )
    lines.append("")

    # Weight scheme definitions
    lines.append("## Weight Scheme Definitions")
    lines.append("")
    for name, weights in config.weight_schemes.items():
        weight_str = ", ".join(f"{k}: {v}" for k, v in weights.items())
        lines.append(f"- **{name}**: {weight_str}")
    lines.append("")
    lines.append(
        "_Volatility penalty is subtracted; all other dimensions are added._  "
        "Dimensions with no available data for an asset are dropped and the "
        "remaining weights renormalize so the score stays on the same scale."
    )
    lines.append("")

    # Honest Limitations — ALWAYS rendered, hard requirement
    lines.append("## Honest Limitations")
    lines.append("")
    lines.append(
        "The following caveats are **always** included in this report because "
        "they affect how results should be interpreted.  Ignoring them can lead "
        "to overconfident conclusions about the screening methodology."
    )
    lines.append("")
    lines.append(
        "1. **Price factors are clean point-in-time.**  "
        "Momentum, volatility, liquidity, and the 200-day moving average are "
        "recomputed from historical closes using only data available up to each "
        "rebalance date.  There is no look-ahead bias in the price factors."
    )
    lines.append("")
    lines.append(
        "2. **Valuation factors are EXCLUDED from this backtest entirely.**  "
        "yfinance provides only the *latest* financial statements (P/E, P/B, "
        "EV/EBITDA, FCF yield, price-to-intrinsic).  Reconstructing historical "
        "valuation scores would require point-in-time fundamental data (e.g. "
        "Compustat), which is not available in this system.  Using the current "
        "statements as a proxy for past ones would introduce severe look-ahead "
        "bias.  Therefore valuation is entirely absent from backtest scoring; "
        "live screening may behave differently from what these results suggest."
    )
    lines.append("")
    lines.append(
        "3. **Survivorship bias.**  "
        "The asset universe is today's active set in the ``assets`` table.  "
        "Companies that were delisted, acquired, or removed from an index during "
        "the backtest window are not present.  This systematically inflates "
        "returns because the historical losers that no longer exist are excluded "
        "from the selection pool."
    )
    lines.append("")
    lines.append(
        "4. **Macro-regime weight tilts are not replayed.**  "
        "In the live system, the Macro Analysis Layer dynamically adjusts "
        "dimension weights based on the current growth/inflation regime.  "
        "This backtest uses *static* weights as defined in each scheme.  "
        "The A/B comparison tests which static weight allocation was historically "
        "better, not whether the dynamic tilt added value — that is a separate "
        "and harder analysis."
    )
    lines.append("")

    return "\n".join(lines)


def _write_csv(csv_path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = ["scheme", "cagr", "sharpe", "max_drawdown", "total_return", "turnover"]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "scheme": row["scheme"],
                "cagr": _fmt_pct(row["cagr"]),
                "sharpe": _fmt_float(row["sharpe"]),
                "max_drawdown": _fmt_pct(row["max_drawdown"]),
                "total_return": _fmt_pct(row["total_return"]),
                "turnover": _fmt_float(row["turnover"], 3),
            })
