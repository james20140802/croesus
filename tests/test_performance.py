from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.performance_check import run_performance_check
from croesus.portfolio.models import Portfolio
from croesus.portfolio.performance import (
    GOAL_AHEAD,
    GOAL_BEHIND,
    GOAL_INSUFFICIENT,
    GOAL_NEAR,
    RISK_OVER,
    RISK_UNKNOWN,
    RISK_WITHIN,
    annualize_return,
    build_performance_period,
    classify_goal,
    classify_risk,
    compute_investment_return,
    max_drawdown,
    minus_months,
    period_start_date,
)
from croesus.portfolio.performance_repository import PerformanceRepository
from croesus.portfolio.repository import PortfolioRepository
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import (
    TXN_DEPOSIT,
    PortfolioTransaction,
)
from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    TradeMode,
)
from croesus.profiles.repository import ProfileRepository

AS_OF = date(2026, 6, 11)


# ── pure: dates ───────────────────────────────────────────────────────────────


def test_minus_months_clamps_day_overflow() -> None:
    # Jan 31 minus one month is the last valid February day, not an error.
    assert minus_months(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert minus_months(date(2026, 1, 15), 1) == date(2025, 12, 15)
    assert minus_months(date(2026, 6, 11), 12) == date(2025, 6, 11)


def test_period_start_date_since_inception_has_no_bound() -> None:
    assert period_start_date(AS_OF, "6m") == date(2025, 12, 11)
    assert period_start_date(AS_OF, "since_inception") is None


# ── pure: contribution-adjusted return ────────────────────────────────────────


def test_deposits_are_not_investment_gain() -> None:
    # Value rose 10k -> 13.2k but 1k of that was a deposit; only 1.2k is return.
    investment_return, adjusted_start, pct = compute_investment_return(
        start_value=11_000, end_value=13_200, net_contributions=1_000
    )
    assert investment_return == 1_200
    assert adjusted_start == 12_000
    assert pct == pytest.approx(0.10)


def test_investment_return_pct_is_none_without_positive_base() -> None:
    # Withdrawing all contributed capital leaves nothing to divide by.
    _, _, pct = compute_investment_return(
        start_value=0, end_value=0, net_contributions=0
    )
    assert pct is None


def test_annualize_return_scales_and_guards_short_windows() -> None:
    assert annualize_return(0.10, 365) == pytest.approx(0.10)
    assert annualize_return(0.10, 182) == pytest.approx((1.10) ** (365 / 182) - 1)
    assert annualize_return(0.05, 10) is None  # too short to annualize
    assert annualize_return(-1.5, 365) == -1.0  # total loss floors at -100%


# ── pure: classification ──────────────────────────────────────────────────────


def test_classify_goal_bands() -> None:
    assert classify_goal(0.15, 0.10) == GOAL_AHEAD
    assert classify_goal(0.105, 0.10) == GOAL_NEAR
    assert classify_goal(0.05, 0.10) == GOAL_BEHIND
    assert classify_goal(None, 0.10) == GOAL_INSUFFICIENT
    assert classify_goal(0.15, None) == GOAL_INSUFFICIENT


def test_classify_risk_precedence() -> None:
    assert classify_risk(
        n_violations=0, n_drift_outside=0, max_drawdown_pct=0.05,
        max_tolerable_drawdown=0.20, has_data=True,
    ) == RISK_WITHIN
    # A hard concentration violation is over budget regardless of drawdown.
    assert classify_risk(
        n_violations=1, n_drift_outside=0, max_drawdown_pct=0.0,
        max_tolerable_drawdown=0.20, has_data=True,
    ) == RISK_OVER
    # Drawdown at/over tolerance is over budget.
    assert classify_risk(
        n_violations=0, n_drift_outside=0, max_drawdown_pct=0.25,
        max_tolerable_drawdown=0.20, has_data=True,
    ) == RISK_OVER
    # No snapshot to assess -> unknown, never a false "within budget".
    assert classify_risk(
        n_violations=0, n_drift_outside=0, max_drawdown_pct=None,
        max_tolerable_drawdown=0.20, has_data=False,
    ) == RISK_UNKNOWN
    # A 0.0 tolerance ("any drawdown breaches") must not be read as "no limit".
    assert classify_risk(
        n_violations=0, n_drift_outside=0, max_drawdown_pct=0.10,
        max_tolerable_drawdown=0.0, has_data=True,
    ) == RISK_OVER


def test_max_drawdown_peak_to_trough() -> None:
    assert max_drawdown([100, 120, 90, 110]) == pytest.approx((120 - 90) / 120)
    assert max_drawdown([100]) is None  # need at least two points


def test_build_period_without_snapshots_is_insufficient_not_fabricated() -> None:
    period = build_performance_period(
        portfolio_id="default", as_of_date=AS_OF, period="1m",
        start_date=None, start_value=None, end_value=None,
        net_contributions=0.0, realized=0.0, dividends=0.0,
        target_return_pct=0.10, max_drawdown_pct=None,
        n_violations=0, n_drift_outside=0, max_tolerable_drawdown=0.20,
        has_risk_data=False,
    )
    assert period.status == GOAL_INSUFFICIENT
    assert period.investment_return is None
    assert period.investment_return_pct is None


# ── integration: run_performance_check ────────────────────────────────────────


def _profile(
    *, expected_annual_return: float = 0.10, max_drawdown: float = 0.20
) -> InvestorProfile:
    return InvestorProfile(
        profile_id="default",
        name="Test",
        base_currency=Currency.USD,
        expected_annual_return=expected_annual_return,
        max_tolerable_drawdown=max_drawdown,
        investment_horizon_years=10,
        monthly_contribution=0.0,
        liquidity_buffer_months=6.0,
        allowed_asset_types=[AssetType.EQUITY, AssetType.ETF, AssetType.CASH],
        disallowed_asset_types=[],
        max_single_position_weight=0.25,
        max_sector_weight=0.4,
        max_industry_weight=0.3,
        max_theme_weight=0.3,
        max_country_weight=0.8,
        max_currency_weight=0.8,
        max_monthly_turnover=0.5,
        rebalance_band=0.05,
        trade_mode=TradeMode.PROPOSE_ONLY,
    )


def _snapshot(conn: duckdb.DuckDBPyConnection, when: date, value: float) -> None:
    PortfolioRepository(conn).save_snapshot("default", when, value)


def _deposit(conn: duckdb.DuckDBPyConnection, txn_id: str, when: date, amount: float) -> None:
    TransactionRepository(conn).record_transaction(
        PortfolioTransaction(
            transaction_id=txn_id,
            portfolio_id="default",
            transaction_date=when,
            transaction_type=TXN_DEPOSIT,
            gross_amount=amount,
            currency="USD",
        )
    )


def _seed_default(conn: duckdb.DuckDBPyConnection) -> None:
    ProfileRepository(conn).upsert_profile(_profile())
    PortfolioRepository(conn).upsert_portfolio(
        Portfolio(
            portfolio_id="default",
            profile_id="default",
            name="Default",
            base_currency="USD",
        )
    )


def test_run_performance_check_excludes_deposits_and_tracks_goal(tmp_path: Path) -> None:
    db_path = tmp_path / "perf.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed_default(conn)
        _deposit(conn, "d0", date(2025, 6, 11), 10_000)
        _deposit(conn, "d1", date(2026, 3, 1), 1_000)  # inside the 6m/1y window
        _snapshot(conn, date(2025, 6, 11), 10_000)
        _snapshot(conn, date(2025, 12, 11), 11_000)
        _snapshot(conn, AS_OF, 13_200)

        result = run_performance_check(
            conn, as_of_date=AS_OF, periods=["6m", "since_inception"], log=lambda _: None
        )
        by_period = {p.period: p for p in result.periods}

        six = by_period["6m"]
        # 13_200 - 11_000 - 1_000 deposit = 1_200 of actual return (not 2_200).
        assert six.investment_return == pytest.approx(1_200)
        assert six.investment_return_pct == pytest.approx(0.10)
        assert six.status == GOAL_AHEAD  # ~21% annualized vs 10% target
        assert six.risk_status == RISK_WITHIN  # no violations, rising value

        sgiven = by_period["since_inception"]
        # start_value 0, all 11k contributed; 13.2k - 11k = 2.2k return = 20%.
        assert sgiven.net_contributions == pytest.approx(11_000)
        assert sgiven.investment_return == pytest.approx(2_200)
        assert sgiven.status == GOAL_AHEAD

        # Persisted and reloadable with the annualized figure intact.
        reloaded = PerformanceRepository(conn).get_period("default", AS_OF, "6m")
    assert reloaded is not None
    assert reloaded.status == GOAL_AHEAD
    assert reloaded.annualized_return_pct == pytest.approx(six.annualized_return_pct)


def test_run_performance_check_behind_goal(tmp_path: Path) -> None:
    db_path = tmp_path / "perf.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        ProfileRepository(conn).upsert_profile(_profile(expected_annual_return=0.30))
        PortfolioRepository(conn).upsert_portfolio(
            Portfolio(portfolio_id="default", profile_id="default",
                      name="d", base_currency="USD")
        )
        _deposit(conn, "d0", date(2025, 6, 11), 10_000)
        _snapshot(conn, date(2025, 6, 11), 10_000)
        _snapshot(conn, AS_OF, 10_500)  # +5% over a year vs 30% target

        result = run_performance_check(
            conn, as_of_date=AS_OF, periods=["since_inception"], log=lambda _: None
        )
    assert result.periods[0].status == GOAL_BEHIND
    assert result.periods[0].return_gap_pct < 0


def test_run_performance_check_insufficient_history(tmp_path: Path) -> None:
    db_path = tmp_path / "perf.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed_default(conn)
        _snapshot(conn, AS_OF, 10_000)  # only one snapshot, today

        result = run_performance_check(
            conn, as_of_date=AS_OF, periods=["1m"], log=lambda _: None
        )
    # No snapshot before the 1m boundary -> honest insufficient_history.
    assert result.periods[0].status == GOAL_INSUFFICIENT
    assert result.periods[0].investment_return_pct is None


def test_run_performance_check_no_snapshots_warns(tmp_path: Path) -> None:
    db_path = tmp_path / "perf.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed_default(conn)
        result = run_performance_check(
            conn, as_of_date=AS_OF, periods=["6m"], log=lambda _: None
        )
    assert result.periods[0].status == GOAL_INSUFFICIENT
    assert result.periods[0].risk_status == RISK_UNKNOWN
    assert any("no portfolio snapshot" in w for w in result.warnings)


def test_run_performance_check_flags_concentration_as_over_budget(tmp_path: Path) -> None:
    db_path = tmp_path / "perf.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed_default(conn)
        _deposit(conn, "d0", date(2025, 6, 11), 10_000)
        _snapshot(conn, date(2025, 6, 11), 10_000)
        _snapshot(conn, AS_OF, 11_000)
        # A live concentration violation at the as-of date.
        conn.execute(
            """
            INSERT INTO portfolio_exposures (
              portfolio_id, as_of_date, exposure_type, exposure_name,
              weight, market_value, limit_weight, is_violation
            ) VALUES ('default', ?, 'single_position', 'US_EQ_AAPL',
                      0.40, 4400, 0.25, TRUE)
            """,
            [AS_OF],
        )
        result = run_performance_check(
            conn, as_of_date=AS_OF, periods=["since_inception"], log=lambda _: None
        )
    assert result.periods[0].risk_status == RISK_OVER


def test_run_performance_check_ignores_null_valued_snapshot(tmp_path: Path) -> None:
    # A snapshot row with NULL total_market_value is missing data, not a $0
    # portfolio: it must not be coerced to 0.0 and fabricate a ~100% drawdown.
    db_path = tmp_path / "perf.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed_default(conn)
        _deposit(conn, "d0", date(2025, 6, 11), 10_000)
        _snapshot(conn, date(2025, 6, 11), 10_000)
        conn.execute(
            "INSERT INTO portfolio_snapshots (portfolio_id, as_of_date, "
            "total_market_value) VALUES ('default', DATE '2026-01-01', NULL)"
        )
        _snapshot(conn, AS_OF, 11_000)  # value only ever rose

        result = run_performance_check(
            conn, as_of_date=AS_OF, periods=["since_inception"], log=lambda _: None
        )
    period = result.periods[0]
    # 10_000 -> 11_000 with a NULL hole in between: no real decline.
    assert period.max_drawdown_pct == 0.0
    assert period.risk_status == RISK_WITHIN  # not over_budget from a phantom crash


def test_run_performance_check_states_goals_not_guarantees(tmp_path: Path) -> None:
    db_path = tmp_path / "perf.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed_default(conn)
        _snapshot(conn, AS_OF, 10_000)
        result = run_performance_check(
            conn, as_of_date=AS_OF, periods=["since_inception"], log=lambda _: None
        )
    assert "goals, not guarantees" in result.disclaimer
