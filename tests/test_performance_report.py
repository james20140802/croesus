from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from dataclasses import replace

from croesus.portfolio.performance import (
    GOAL_AHEAD,
    GOAL_INSUFFICIENT,
    RISK_OVER,
    RISK_WITHIN,
    Attribution,
    PerformanceCheckResult,
    PerformancePeriod,
)
from croesus.reports.performance import render_markdown, write_performance_reports

AS_OF = date(2026, 6, 11)


def _result() -> PerformanceCheckResult:
    six = PerformancePeriod(
        portfolio_id="default",
        as_of_date=AS_OF,
        period="6m",
        start_value=11_000.0,
        end_value=13_200.0,
        net_contributions=1_000.0,
        investment_return=1_200.0,
        investment_return_pct=0.10,
        annualized_return_pct=0.21,
        target_return_pct=0.10,
        return_gap_pct=0.11,
        max_drawdown_pct=0.05,
        risk_status=RISK_WITHIN,
        status=GOAL_AHEAD,
        attribution=Attribution(
            net_contributions=1_000.0,
            realized=0.0,
            dividends=25.0,
            market_movement=1_175.0,
            notes=["approximate"],
        ),
    )
    one_month = PerformancePeriod(
        portfolio_id="default",
        as_of_date=AS_OF,
        period="1m",
        start_value=None,
        end_value=None,
        net_contributions=0.0,
        investment_return=None,
        investment_return_pct=None,
        annualized_return_pct=None,
        target_return_pct=0.10,
        return_gap_pct=None,
        max_drawdown_pct=None,
        risk_status="unknown",
        status=GOAL_INSUFFICIENT,
        attribution=Attribution(
            net_contributions=0.0, realized=0.0, dividends=0.0,
            market_movement=None, notes=[],
        ),
    )
    return PerformanceCheckResult(
        portfolio_id="default",
        as_of_date=AS_OF,
        periods=[six, one_month],
        warnings=["net contributions mix currencies ['EUR'] with base USD"],
    )


def test_render_markdown_includes_goal_risk_and_disclaimer() -> None:
    md = render_markdown(_result())
    assert "# Performance and Goal Progress - 2026-06-11" in md
    assert "goals, not guarantees" in md  # the disclaimer is always present
    assert "ahead of goal" in md
    assert "within budget" in md
    assert "insufficient history" in md  # the 1m period is rendered honestly
    # Attribution only appears for periods with a real return.
    assert "Market movement" in md
    assert "Dividends: 25.00" in md


def test_reports_name_the_risk_drivers(tmp_path: Path) -> None:
    # over_budget beside drawdown=0.0% must explain itself: the Risk cell and
    # the CSV carry the drivers, so the verdict never reads as a contradiction.
    base = _result()
    over = replace(
        base.periods[0],
        max_drawdown_pct=0.0,
        risk_status=RISK_OVER,
        risk_reasons=["8 concentration violations"],
    )
    result = PerformanceCheckResult(
        portfolio_id=base.portfolio_id,
        as_of_date=base.as_of_date,
        periods=[over, base.periods[1]],
    )

    md = render_markdown(result)
    assert "over budget — 8 concentration violations" in md

    _, csv_path = write_performance_reports(result, reports_dir=tmp_path)
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_period = {r["period"]: r for r in rows}
    assert by_period["6m"]["risk_reasons"] == "8 concentration violations"
    assert by_period["1m"]["risk_reasons"] == ""


def test_render_markdown_surfaces_warnings() -> None:
    md = render_markdown(_result())
    assert "## Warnings" in md
    assert "mix currencies" in md


def test_write_performance_reports_emits_md_and_csv(tmp_path: Path) -> None:
    md_path, csv_path = write_performance_reports(_result(), reports_dir=tmp_path)
    assert md_path.exists() and csv_path.exists()

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_period = {r["period"]: r for r in rows}
    assert by_period["6m"]["status"] == GOAL_AHEAD
    assert by_period["6m"]["investment_return"] == "1200.0"
    # The insufficient period writes blanks, not a fabricated zero return.
    assert by_period["1m"]["investment_return_pct"] == ""
    assert by_period["1m"]["status"] == GOAL_INSUFFICIENT
