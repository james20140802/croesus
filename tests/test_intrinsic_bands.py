"""Tests for Phase C3: thesis-grade intrinsic-value bands."""
from __future__ import annotations

import pandas as pd
from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


# ---------------------------------------------------------------------------
# Task 1: scenario_knobs
# ---------------------------------------------------------------------------

def test_scenario_knobs_base_maps_grades() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs

    base = scenario_knobs(moat="narrow", sector="stable", disruption="medium", scenario="base")
    assert base.explicit_years == 7          # CAP_YEARS["narrow"]
    assert base.terminal_growth_rate == 0.025  # TERMINAL_GROWTH["stable"]
    assert base.wacc_risk_premium == 0.01    # RISK_PREMIUM["medium"]


def test_scenario_knobs_bear_and_bull_step_one_notch() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs

    bear = scenario_knobs(moat="narrow", sector="stable", disruption="medium", scenario="bear")
    # moat narrow->none (CAP 5), sector stable->declining (0.015), disruption medium->high (0.02)
    assert bear.explicit_years == 5
    assert bear.terminal_growth_rate == 0.015
    assert bear.wacc_risk_premium == 0.02

    bull = scenario_knobs(moat="narrow", sector="stable", disruption="medium", scenario="bull")
    # moat narrow->wide (CAP 10), sector stable->secular_growth (0.030), disruption medium->low (0.0)
    assert bull.explicit_years == 10
    assert bull.terminal_growth_rate == 0.030
    assert bull.wacc_risk_premium == 0.0


def test_scenario_knobs_clamps_at_ends() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs

    # wide moat can't get wider; low disruption can't get safer.
    bull = scenario_knobs(moat="wide", sector="secular_growth", disruption="low", scenario="bull")
    assert bull.explicit_years == 10 and bull.terminal_growth_rate == 0.030
    assert bull.wacc_risk_premium == 0.0
    bear = scenario_knobs(moat="none", sector="declining", disruption="high", scenario="bear")
    assert bear.explicit_years == 5 and bear.terminal_growth_rate == 0.015
    assert bear.wacc_risk_premium == 0.02


def test_scenario_knobs_none_grade_falls_back_to_defaults() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs
    from croesus.factors.equity.valuation import DEFAULT_DCF_KNOBS

    base = scenario_knobs(moat=None, sector=None, disruption=None, scenario="base")
    assert base.explicit_years == DEFAULT_DCF_KNOBS.explicit_years          # none -> 5
    assert base.terminal_growth_rate == DEFAULT_DCF_KNOBS.terminal_growth_rate  # stable -> 0.025
    assert base.wacc_risk_premium == DEFAULT_DCF_KNOBS.wacc_risk_premium    # low -> 0.0


# ---------------------------------------------------------------------------
# Task 2: load_latest_for_asset
# ---------------------------------------------------------------------------

def test_load_latest_for_asset_returns_most_recent_generated(tmp_path: Path) -> None:
    from croesus.research.thesis_models import (
        STATUS_FAILED,
        STATUS_GENERATED,
        ThesisGrade,
    )
    from croesus.research.thesis_repository import ThesisGradeRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    def _grade(d: date, status: str, moat: str | None) -> ThesisGrade:
        return ThesisGrade(
            asset_id="US_EQ_AAPL", as_of_date=d, run_id="r", model="m",
            status=status, moat_grade=moat, sector_grade="stable",
            disruption_grade="low",
        )

    with get_connection(db_path) as conn:
        repo = ThesisGradeRepository(conn)
        repo.upsert(_grade(date(2026, 5, 1), STATUS_GENERATED, "narrow"))
        repo.upsert(_grade(date(2026, 6, 1), STATUS_GENERATED, "wide"))

        latest = repo.load_latest_for_asset("US_EQ_AAPL", date(2026, 6, 19))
        assert latest is not None and latest.moat_grade == "wide"
        # Range-bounded: nothing on or before an earlier date than the first grade.
        assert repo.load_latest_for_asset("US_EQ_AAPL", date(2026, 1, 1)) is None
        # A failed grade is ignored even if it's the most recent.
        repo.upsert(_grade(date(2026, 6, 10), STATUS_FAILED, None))
        still = repo.load_latest_for_asset("US_EQ_AAPL", date(2026, 6, 19))
        assert still.as_of_date == date(2026, 6, 1) and still.moat_grade == "wide"


# ---------------------------------------------------------------------------
# Task 3: compute_intrinsic_bands
# ---------------------------------------------------------------------------

def test_compute_intrinsic_bands_orders_bear_base_bull() -> None:
    from croesus.factors.equity.intrinsic_bands import (
        SCENARIOS,
        compute_intrinsic_bands,
    )

    bands = compute_intrinsic_bands(
        base_fcf=1.0e9, growth=0.08, risk_free_rate=0.045, beta=1.0,
        shares_outstanding=1.0e8, total_debt=0.0, cash=0.0,
        moat="narrow", sector="stable", disruption="medium",
    )
    assert set(bands) == set(SCENARIOS) == {"bear", "base", "bull"}
    # A wider moat / higher terminal / lower premium must not value LOWER than bear.
    iv = {s: b.intrinsic_value_per_share for s, b in bands.items() if b is not None}
    assert iv["bull"] >= iv["base"] >= iv["bear"]
    # Knobs are recorded per scenario for persistence/audit.
    assert bands["bull"].explicit_years == 10
    assert bands["bear"].wacc_risk_premium == 0.02


# ---------------------------------------------------------------------------
# Task 4: band repository
# ---------------------------------------------------------------------------

def test_band_repository_upserts_three_scenarios(tmp_path: Path) -> None:
    from croesus.factors.equity.band_repository import (
        BandRow,
        IntrinsicValueBandRepository,
    )

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 19)

    def _row(scenario: str, iv: float) -> BandRow:
        return BandRow(
            asset_id="US_EQ_AAPL", date=asof, scenario=scenario,
            intrinsic_value_per_share=iv, current_price=100.0,
            upside_pct=iv / 100.0 - 1.0, wacc=0.09, fcf_growth_rate=0.08,
            terminal_growth_rate=0.025, explicit_years=7, wacc_risk_premium=0.01,
            moat_grade="narrow", sector_grade="stable", disruption_grade="medium",
            thesis_as_of_date=asof, thesis_run_id="run-1",
        )

    with get_connection(db_path) as conn:
        repo = IntrinsicValueBandRepository(conn)
        repo.upsert_band(_row("bear", 80.0))
        repo.upsert_band(_row("base", 120.0))
        repo.upsert_band(_row("bull", 160.0))
        # Re-grade overwrites in place.
        repo.upsert_band(_row("base", 130.0))

        rows = repo.load_for_asset("US_EQ_AAPL", asof)
        by_scenario = {r.scenario: r for r in rows}
    assert set(by_scenario) == {"bear", "base", "bull"}
    assert by_scenario["base"].intrinsic_value_per_share == 130.0
    assert by_scenario["bull"].explicit_years == 7
    assert by_scenario["bear"].moat_grade == "narrow"
    assert len(rows) == 3  # idempotent: base overwritten, not duplicated


# ---------------------------------------------------------------------------
# Task 5: integration — band wired into quarterly DCF pass
# ---------------------------------------------------------------------------

_AS_OF_C3 = date(2026, 6, 1)


def _price_frame(close: float) -> pd.DataFrame:
    return pd.DataFrame([
        {"date": date(2026, 5, 29), "open": close, "high": close, "low": close,
         "close": close, "adjusted_close": close, "volume": 1000},
        {"date": _AS_OF_C3, "open": close, "high": close, "low": close,
         "close": close, "adjusted_close": close, "volume": 1000},
    ])


def _fcf_fundamentals(asset_id: str, fcf: list[float]):
    from croesus.fundamentals.repository import (
        METRIC_CASH_AND_EQUIVALENTS,
        METRIC_FREE_CASH_FLOW,
        METRIC_SHARES_OUTSTANDING,
        METRIC_TOTAL_DEBT,
        PERIOD_ANNUAL,
        FundamentalMetric,
    )

    years = [date(2022, 12, 31), date(2023, 12, 31), date(2024, 12, 31)]
    rows = [
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_TOTAL_DEBT, 0.0, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_CASH_AND_EQUIVALENTS, 0.0, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_SHARES_OUTSTANDING, 10.0, "t"),
    ]
    for year, value in zip(years, fcf):
        rows.append(FundamentalMetric(asset_id, year, PERIOD_ANNUAL, METRIC_FREE_CASH_FLOW, value, "t"))
    return rows


def test_compute_valuation_writes_band_only_for_graded_assets(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.factors.equity.band_repository import IntrinsicValueBandRepository
    from croesus.factors.equity.compute_valuation import (
        compute_and_store_valuation_factors,
    )
    from croesus.factors.equity.repository import ValuationSnapshotRepository
    from croesus.fundamentals.repository import FundamentalsRepository
    from croesus.prices.repository import PriceRepository
    from croesus.research.thesis_models import STATUS_GENERATED, ThesisGrade
    from croesus.research.thesis_repository import ThesisGradeRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL, MSFT, NVDA (all US equities)
        prices = PriceRepository(conn)
        prices.upsert_daily_prices("US_EQ_AAPL", _price_frame(100.0), source="test")
        prices.upsert_daily_prices("US_EQ_MSFT", _price_frame(200.0), source="test")
        funds = FundamentalsRepository(conn)
        funds.upsert_metrics(_fcf_fundamentals("US_EQ_AAPL", [30.0, 40.0, 50.0]))
        funds.upsert_metrics(_fcf_fundamentals("US_EQ_MSFT", [40.0, 50.0, 60.0]))
        # AAPL is graded; MSFT is not.
        ThesisGradeRepository(conn).upsert(ThesisGrade(
            asset_id="US_EQ_AAPL", as_of_date=_AS_OF_C3, run_id="r", model="m",
            status=STATUS_GENERATED, moat_grade="wide", sector_grade="secular_growth",
            disruption_grade="low",
        ))

        compute_and_store_valuation_factors(conn, include_dcf=True, as_of=_AS_OF_C3)

        band_repo = IntrinsicValueBandRepository(conn)
        graded_bands = band_repo.load_for_asset("US_EQ_AAPL", _AS_OF_C3)
        ungraded_bands = band_repo.load_for_asset("US_EQ_MSFT", _AS_OF_C3)
        # The base valuation snapshot must still be the mechanical default-knob DCF.
        snap = ValuationSnapshotRepository(conn).get("US_EQ_AAPL", _AS_OF_C3)

    assert {b.scenario for b in graded_bands} == {"bear", "base", "bull"}
    assert ungraded_bands == []          # grade-only: no thesis -> no band
    # Base snapshot uses DEFAULT knobs (explicit_years 5), NOT the wide-moat 10.
    assert snap is not None and snap.assumptions["explicit_years"] == 5
