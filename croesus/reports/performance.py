"""
Performance and goal-tracking reports (Sprint 006d).

Renders a :class:`PerformanceCheckResult` to Markdown and CSV. Pure with
respect to the database — it formats the structured result the job already
produced, so the CLI, a report file, and a future dashboard all show the same
numbers. Every rendered surface repeats that target returns are goals, not
guarantees.
"""
from __future__ import annotations

import csv
from pathlib import Path

from croesus.portfolio.performance import (
    GOAL_AHEAD,
    GOAL_BEHIND,
    GOAL_INSUFFICIENT,
    GOAL_NEAR,
    PerformanceCheckResult,
    PerformancePeriod,
)

_GOAL_LABELS = {
    GOAL_AHEAD: "ahead of goal",
    GOAL_NEAR: "near goal",
    GOAL_BEHIND: "behind goal",
    GOAL_INSUFFICIENT: "insufficient history",
}

_CSV_FIELDS = [
    "period",
    "start_value",
    "end_value",
    "net_contributions",
    "investment_return",
    "investment_return_pct",
    "annualized_return_pct",
    "target_return_pct",
    "return_gap_pct",
    "max_drawdown_pct",
    "risk_status",
    "status",
]


def write_performance_reports(
    result: PerformanceCheckResult,
    *,
    reports_dir: str | Path = "reports",
) -> tuple[Path, Path]:
    """Write Markdown and CSV views of ``result``; return their paths."""
    output_dir = Path(reports_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{result.as_of_date:%Y-%m-%d}"
    markdown_path = output_dir / f"performance_{stamp}.md"
    csv_path = output_dir / f"performance_{stamp}.csv"

    markdown_path.write_text(render_markdown(result), encoding="utf-8")
    _write_csv(csv_path, result)
    return markdown_path, csv_path


def render_markdown(result: PerformanceCheckResult) -> str:
    """Render ``result`` as a Markdown progress report."""
    lines = [
        f"# Performance and Goal Progress - {result.as_of_date:%Y-%m-%d}",
        "",
        f"- Portfolio: {result.portfolio_id}",
        "",
        f"> {result.disclaimer}",
        "",
        "## Goal Progress by Period",
        "",
        "| Period | Goal | Return | Annualized | Target | Gap | Risk | Drawdown |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for period in result.periods:
        lines.append(
            "| {period} | {goal} | {ret} | {ann} | {target} | {gap} | {risk} | {dd} |".format(
                period=period.period,
                goal=_GOAL_LABELS.get(period.status, period.status),
                ret=_pct(period.investment_return_pct),
                ann=_pct(period.annualized_return_pct),
                target=_pct(period.target_return_pct),
                gap=_pct(period.return_gap_pct),
                risk=period.risk_status.replace("_", " "),
                dd=_pct(period.max_drawdown_pct),
            )
        )

    lines.extend(["", "## Attribution", ""])
    for period in result.periods:
        if period.investment_return is None:
            continue
        attribution = period.attribution
        lines.append(f"### {period.period}")
        lines.append(f"- Market movement: {_money(attribution.market_movement)}")
        lines.append(f"- Net contributions: {_money(attribution.net_contributions)}")
        lines.append(f"- Realized P&L: {_money(attribution.realized)}")
        lines.append(f"- Dividends: {_money(attribution.dividends)}")
        for note in attribution.notes:
            lines.append(f"- Note: {note}")
        lines.append("")

    if result.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in result.warnings)
        lines.append("")

    return "\n".join(lines)


def _write_csv(path: Path, result: PerformanceCheckResult) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for period in result.periods:
            writer.writerow(_csv_row(period))


def _csv_row(period: PerformancePeriod) -> dict[str, object]:
    return {
        "period": period.period,
        "start_value": period.start_value,
        "end_value": period.end_value,
        "net_contributions": period.net_contributions,
        "investment_return": period.investment_return,
        "investment_return_pct": period.investment_return_pct,
        "annualized_return_pct": period.annualized_return_pct,
        "target_return_pct": period.target_return_pct,
        "return_gap_pct": period.return_gap_pct,
        "max_drawdown_pct": period.max_drawdown_pct,
        "risk_status": period.risk_status,
        "status": period.status,
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"
