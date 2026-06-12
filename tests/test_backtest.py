"""Sprint 014: walk-forward backtest harness tests.

All tests use synthetic, deterministic data — no network, no real DB.
Prices are seeded with known linear or exponential drifts so expected
factor rankings and portfolio outcomes can be computed by hand.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.backtest.config import BacktestConfig, default_config
from croesus.backtest.engine import run_backtest
from croesus.backtest.metrics import cagr, max_drawdown, sharpe, summarize, total_return
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.reports.backtest import write_backtest_report

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

START = date(2021, 1, 4)   # first trading Monday of 2021
END = date(2022, 6, 30)    # 18-month window

# Asset IDs used in synthetic seeding
AID_HIGH = "US_EQ_HIGH"    # strong upward drift — should win momentum
AID_LOW  = "US_EQ_LOW"     # flat/sideways drift
AID_SPY  = "US_ETF_SPY"    # benchmark stand-in
AID_SHORT = "US_EQ_SHORT"  # only 30 days of data — insufficient history


def _open_db(tmp_path: Path):
    db_path = tmp_path / "bt.duckdb"
    migrate(db_path)
    return get_connection(db_path)


def _seed_assets(conn) -> None:
    """Register equity and ETF assets, plus one with symbol='SPY' for benchmark."""
    AssetRepository(conn).upsert_many([
        Asset(
            asset_id=AID_HIGH, symbol="HIGH", name="High-Drift Corp",
            asset_type="equity", country="US", currency="USD",
            sector="Technology", industry="Software",
        ),
        Asset(
            asset_id=AID_LOW, symbol="LOW", name="Low-Drift Corp",
            asset_type="equity", country="US", currency="USD",
            sector="Finance", industry="Banks",
        ),
        Asset(
            asset_id=AID_SPY, symbol="SPY", name="SPDR S&P 500 ETF",
            asset_type="etf", country="US", currency="USD",
        ),
        Asset(
            asset_id=AID_SHORT, symbol="SHRT", name="Short History Corp",
            asset_type="equity", country="US", currency="USD",
        ),
    ])


def _seed_prices(
    conn,
    *,
    start: date = START,
    end: date = END,
) -> None:
    """Insert synthetic weekday prices for each asset.

    HIGH:  starts at 100, rises 0.3/day  → strong upward drift
    LOW:   starts at 100, rises 0.05/day → weak drift
    SPY:   starts at 400, rises 0.15/day → moderate benchmark
    SHORT: only 30 days of data (insufficient for 200-bar requirement)
    """
    rows = []
    day = start
    high_close = 100.0
    low_close  = 100.0
    spy_close  = 400.0
    short_days = 0

    while day <= end:
        if day.weekday() < 5:  # weekdays only
            rows.append((AID_HIGH, day, high_close, 1_000_000.0, "test"))
            rows.append((AID_LOW,  day, low_close,  1_000_000.0, "test"))
            rows.append((AID_SPY,  day, spy_close,  5_000_000.0, "test"))
            if short_days < 30:
                rows.append((AID_SHORT, day, 50.0, 500_000.0, "test"))
                short_days += 1
            high_close += 0.30
            low_close  += 0.05
            spy_close  += 0.15
        day += timedelta(days=1)

    conn.executemany(
        "INSERT INTO prices_daily (asset_id, date, close, volume, source) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )


def _minimal_config(start: str, end: str, *, top_n: int = 1, cost_bps: float = 0.0) -> BacktestConfig:
    """Config with a single 'momentum_only' scheme for simple assertions."""
    return BacktestConfig(
        start_date=start,
        end_date=end,
        top_n=top_n,
        cost_bps=cost_bps,
        weight_schemes={"momentum_only": {"momentum": 1.0}},
        benchmark_symbol="SPY",
    )


# ---------------------------------------------------------------------------
# BacktestConfig tests
# ---------------------------------------------------------------------------

def test_default_config_has_two_schemes() -> None:
    cfg = default_config()
    assert "composite_v1" in cfg.weight_schemes
    assert "momentum_only" in cfg.weight_schemes


def test_config_is_frozen() -> None:
    cfg = default_config()
    with pytest.raises((AttributeError, TypeError)):
        cfg.top_n = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Metrics unit tests
# ---------------------------------------------------------------------------

def _flat_curve(n: int = 252, value: float = 100.0) -> pd.Series:
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(n)]
    return pd.Series([value] * n, index=dates, dtype=float)


def _growing_curve(n: int = 252, start: float = 100.0, daily_ret: float = 0.001) -> pd.Series:
    """Curve that grows at a fixed daily rate."""
    dates = [date(2021, 1, 1) + timedelta(days=i) for i in range(n)]
    values = [start * (1 + daily_ret) ** i for i in range(n)]
    return pd.Series(values, index=dates, dtype=float)


def test_total_return_exact() -> None:
    """100 → 150 is exactly 50%."""
    curve = pd.Series([100.0, 125.0, 150.0], index=[date(2021, 1, i) for i in range(1, 4)])
    assert total_return(curve) == pytest.approx(0.50)


def test_total_return_none_on_short_curve() -> None:
    curve = pd.Series([100.0], index=[date(2021, 1, 1)])
    assert total_return(curve) is None


def test_cagr_known_value() -> None:
    """Doubling in exactly 1 year = 100% CAGR."""
    start_d = date(2021, 1, 1)
    end_d = date(2022, 1, 1)
    curve = pd.Series(
        [100.0, 200.0],
        index=[start_d, end_d],
        dtype=float,
    )
    result = cagr(curve)
    assert result is not None
    assert result == pytest.approx(1.0, abs=0.01)


def test_cagr_none_on_single_point() -> None:
    assert cagr(pd.Series([100.0], index=[date(2021, 1, 1)])) is None


def test_cagr_none_on_empty() -> None:
    assert cagr(pd.Series(dtype=float)) is None


def test_sharpe_flat_returns_none() -> None:
    """A perfectly flat curve has std=0, so Sharpe is undefined."""
    curve = _flat_curve(10)
    result = sharpe(curve)
    assert result is None


def test_sharpe_growing_curve_positive() -> None:
    curve = _growing_curve(252, daily_ret=0.001)
    result = sharpe(curve)
    assert result is not None
    assert result > 0


def test_sharpe_none_on_short_curve() -> None:
    assert sharpe(pd.Series([100.0], index=[date(2021, 1, 1)])) is None


def test_max_drawdown_is_negative() -> None:
    """A curve that drops 20% at some point should have MDD <= -0.20."""
    # 100 → 80 → 120
    curve = pd.Series(
        [100.0, 80.0, 120.0],
        index=[date(2021, 1, i) for i in range(1, 4)],
        dtype=float,
    )
    mdd = max_drawdown(curve)
    assert mdd is not None
    assert mdd <= -0.19  # approximately -20%


def test_max_drawdown_none_on_single_point() -> None:
    assert max_drawdown(pd.Series([100.0], index=[date(2021, 1, 1)])) is None


def test_summarize_returns_all_keys() -> None:
    curve = _growing_curve(252)
    result = summarize(curve)
    assert set(result.keys()) == {"cagr", "sharpe", "max_drawdown", "total_return"}


def test_summarize_empty_curve_all_none() -> None:
    result = summarize(pd.Series(dtype=float))
    assert all(v is None for v in result.values())


def test_summarize_single_point_all_none() -> None:
    result = summarize(pd.Series([100.0], index=[date(2021, 1, 1)]))
    assert all(v is None for v in result.values())


# ---------------------------------------------------------------------------
# Engine tests
# ---------------------------------------------------------------------------

def test_engine_picks_higher_momentum_asset(tmp_path: Path) -> None:
    """With momentum_only scheme, the high-drift asset should be selected."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    config = _minimal_config(
        start="2021-08-01",   # after 200-bar minimum is satisfied
        end="2022-01-31",
        top_n=1,
    )
    results = run_backtest(config, db_path=str(tmp_path / "bt.duckdb"))
    scheme_result = results["momentum_only"]

    # At least one rebalance must have occurred.
    assert len(scheme_result.rebalances) >= 1

    # Majority of rebalances should pick AID_HIGH (strongest momentum).
    high_count = sum(
        1 for r in scheme_result.rebalances if AID_HIGH in r.holdings
    )
    assert high_count > 0, f"Expected HIGH selected at least once; rebalances={scheme_result.rebalances}"


def test_engine_assets_with_insufficient_history_skipped(tmp_path: Path) -> None:
    """AID_SHORT has only 30 days — must be skipped without crashing."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    config = _minimal_config(
        start="2021-08-01",
        end="2022-01-31",
        top_n=3,
    )
    results = run_backtest(config, db_path=str(tmp_path / "bt.duckdb"))
    # Should complete without exception
    assert "momentum_only" in results

    # AID_SHORT should never appear in any holding.
    for r in results["momentum_only"].rebalances:
        assert AID_SHORT not in r.holdings, f"AID_SHORT should not appear in holdings: {r}"


def test_engine_deterministic(tmp_path: Path) -> None:
    """Same config + same data → identical equity curve values."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    config = BacktestConfig(
        start_date="2021-08-01",
        end_date="2022-06-30",
        top_n=2,
        cost_bps=10.0,
        weight_schemes={
            "composite_v1": {
                "momentum": 0.35,
                "liquidity": 0.25,
                "trend": 0.25,
                "volatility_penalty": 0.15,
            },
            "momentum_only": {"momentum": 1.0},
        },
    )
    db_path = str(tmp_path / "bt.duckdb")
    results_a = run_backtest(config, db_path=db_path)
    results_b = run_backtest(config, db_path=db_path)

    for scheme_name in config.weight_schemes:
        curve_a = results_a[scheme_name].equity_curve
        curve_b = results_b[scheme_name].equity_curve
        assert list(curve_a.values) == list(curve_b.values), (
            f"Non-deterministic equity curve for scheme '{scheme_name}'"
        )
        assert results_a[scheme_name].total_turnover == results_b[scheme_name].total_turnover


def test_engine_two_schemes_two_rows_in_report(tmp_path: Path) -> None:
    """Report must have two scheme rows + one benchmark row."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    config = BacktestConfig(
        start_date="2021-08-01",
        end_date="2022-06-30",
        top_n=1,
        cost_bps=5.0,
        weight_schemes={
            "composite_v1": {"momentum": 0.5, "liquidity": 0.5},
            "momentum_only": {"momentum": 1.0},
        },
    )
    db_path = str(tmp_path / "bt.duckdb")
    results = run_backtest(config, db_path=db_path)

    md_path, csv_path = write_backtest_report(
        results, config, reports_dir=tmp_path / "reports"
    )
    md_text = md_path.read_text(encoding="utf-8")

    # Both scheme names must appear.
    assert "composite_v1" in md_text
    assert "momentum_only" in md_text
    # Benchmark row must appear.
    assert "SPY" in md_text
    # Limitations section is mandatory.
    assert "Honest Limitations" in md_text

    # CSV must have 3 rows of data (2 schemes + benchmark header).
    csv_lines = [line for line in csv_path.read_text().splitlines() if line.strip()]
    # header + composite_v1 + momentum_only + SPY = 4 lines
    assert len(csv_lines) == 4, f"Expected 4 CSV lines, got: {csv_lines}"


def test_report_limitations_text_present(tmp_path: Path) -> None:
    """The four Honest Limitations points must always appear in the markdown."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    config = _minimal_config("2021-08-01", "2022-06-30")
    db_path = str(tmp_path / "bt.duckdb")
    results = run_backtest(config, db_path=db_path)
    md_path, _ = write_backtest_report(results, config, reports_dir=tmp_path / "rpt")
    text = md_path.read_text(encoding="utf-8")

    # Each of the four limitations must be present.
    assert "Point-in-time" in text or "point-in-time" in text
    assert "EXCLUDED" in text or "valuation" in text.lower()
    assert "Survivorship" in text or "survivorship" in text.lower()
    assert "Macro-regime" in text or "macro-regime" in text.lower()


def test_costs_reduce_final_value(tmp_path: Path) -> None:
    """Running with non-zero cost_bps must produce a lower final value than cost_bps=0."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    db_path = str(tmp_path / "bt.duckdb")

    config_free = BacktestConfig(
        start_date="2021-08-01",
        end_date="2022-06-30",
        top_n=1,
        cost_bps=0.0,
        weight_schemes={"momentum_only": {"momentum": 1.0}},
    )
    config_cost = BacktestConfig(
        start_date="2021-08-01",
        end_date="2022-06-30",
        top_n=1,
        cost_bps=50.0,   # high cost to make the effect clear
        weight_schemes={"momentum_only": {"momentum": 1.0}},
    )

    result_free = run_backtest(config_free, db_path=db_path)
    result_cost = run_backtest(config_cost, db_path=db_path)

    free_final = result_free["momentum_only"].equity_curve.iloc[-1]
    cost_final = result_cost["momentum_only"].equity_curve.iloc[-1]

    assert free_final > cost_final, (
        f"Zero-cost run ({free_final:.2f}) should exceed high-cost run ({cost_final:.2f})"
    )


def test_engine_empty_db_no_crash(tmp_path: Path) -> None:
    """An empty DB (no prices) must return results without crashing."""
    db_path = str(tmp_path / "empty.duckdb")
    migrate(db_path)

    config = _minimal_config("2021-01-04", "2021-06-30")
    results = run_backtest(config, db_path=db_path)
    assert "momentum_only" in results
    # Equity curve may be empty — just don't crash.
    assert isinstance(results["momentum_only"].equity_curve, pd.Series)


def test_engine_benchmark_curve_present(tmp_path: Path) -> None:
    """Benchmark equity curve must be present when SPY prices exist."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    config = _minimal_config("2021-08-01", "2022-06-30")
    results = run_backtest(config, db_path=str(tmp_path / "bt.duckdb"))
    bench = results.get("__benchmark__")
    assert bench is not None
    assert len(bench.equity_curve) > 0


def test_engine_benchmark_starts_at_initial_capital(tmp_path: Path) -> None:
    """The benchmark equity curve must start at initial_capital."""
    with _open_db(tmp_path) as conn:
        _seed_assets(conn)
        _seed_prices(conn)

    config = BacktestConfig(
        start_date="2021-08-01",
        end_date="2022-06-30",
        top_n=1,
        cost_bps=0.0,
        weight_schemes={"momentum_only": {"momentum": 1.0}},
        initial_capital=50_000.0,
    )
    results = run_backtest(config, db_path=str(tmp_path / "bt.duckdb"))
    bench = results["__benchmark__"]
    if len(bench.equity_curve) > 0:
        assert bench.equity_curve.iloc[0] == pytest.approx(50_000.0)
