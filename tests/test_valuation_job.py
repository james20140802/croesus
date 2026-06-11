from datetime import date
from pathlib import Path

import pandas as pd

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.equity.compute_valuation import (
    compute_and_store_valuation_factors,
)
from croesus.fundamentals.repository import (
    METRIC_BOOK_VALUE_PER_SHARE,
    METRIC_CASH_AND_EQUIVALENTS,
    METRIC_EBITDA,
    METRIC_EPS,
    METRIC_FREE_CASH_FLOW,
    METRIC_SHARES_OUTSTANDING,
    METRIC_TOTAL_DEBT,
    PERIOD_ANNUAL,
    FundamentalMetric,
    FundamentalsRepository,
)
from croesus.prices.repository import PriceRepository

AS_OF = date(2026, 6, 1)


def _price_frame(close: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": date(2026, 5, 29), "open": close, "high": close, "low": close,
             "close": close, "adjusted_close": close, "volume": 1000},
            {"date": AS_OF, "open": close, "high": close, "low": close,
             "close": close, "adjusted_close": close, "volume": 1000},
        ]
    )


def _fundamentals(asset_id: str, *, eps, bvps, ebitda, debt, cash, shares, fcf) -> list[FundamentalMetric]:
    years = [date(2022, 12, 31), date(2023, 12, 31), date(2024, 12, 31)]
    rows = [
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_EPS, eps, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_BOOK_VALUE_PER_SHARE, bvps, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_EBITDA, ebitda, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_TOTAL_DEBT, debt, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_CASH_AND_EQUIVALENTS, cash, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_SHARES_OUTSTANDING, shares, "t"),
    ]
    for year, value in zip(years, fcf):
        rows.append(FundamentalMetric(asset_id, year, PERIOD_ANNUAL, METRIC_FREE_CASH_FLOW, value, "t"))
    return rows


def _seed(conn) -> None:
    seed_us_equities(conn)  # AAPL, MSFT, NVDA — all Technology
    prices = PriceRepository(conn)
    prices.upsert_daily_prices("US_EQ_AAPL", _price_frame(100.0), source="test")
    prices.upsert_daily_prices("US_EQ_MSFT", _price_frame(200.0), source="test")
    prices.upsert_daily_prices("US_EQ_NVDA", _price_frame(50.0), source="test")  # price but no fundamentals

    repo = FundamentalsRepository(conn)
    repo.upsert_metrics(
        _fundamentals("US_EQ_AAPL", eps=5.0, bvps=25.0, ebitda=110.0, debt=200.0, cash=100.0, shares=10.0, fcf=[30.0, 40.0, 50.0])
    )
    repo.upsert_metrics(
        _fundamentals("US_EQ_MSFT", eps=10.0, bvps=50.0, ebitda=100.0, debt=100.0, cash=50.0, shares=5.0, fcf=[40.0, 50.0, 60.0])
    )


def test_valuation_writes_eight_factors_and_dcf_snapshots(tmp_path: Path) -> None:
    db_path = tmp_path / "v.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed(conn)
        result = compute_and_store_valuation_factors(conn, include_dcf=True, as_of=AS_OF)

        factors = {
            (row[0], row[1]): row[2]
            for row in conn.execute(
                "SELECT asset_id, factor_name, value FROM factor_values WHERE date = ?",
                [AS_OF],
            ).fetchall()
        }

    # AAPL: all four multiples present and correct.
    assert factors[("US_EQ_AAPL", "pe_ratio")] == 20.0
    assert factors[("US_EQ_AAPL", "pb_ratio")] == 4.0
    assert factors[("US_EQ_AAPL", "ev_to_ebitda")] == 10.0
    assert factors[("US_EQ_AAPL", "fcf_yield")] == 0.05
    # Sector percentiles present and bounded.
    for pct in ("pe_vs_sector_pct", "pb_vs_sector_pct", "ev_ebitda_vs_sector_pct"):
        assert 0.0 <= factors[("US_EQ_AAPL", pct)] <= 100.0
    # DCF produced a price_to_intrinsic for both valued names.
    assert ("US_EQ_AAPL", "price_to_intrinsic") in factors
    assert ("US_EQ_MSFT", "price_to_intrinsic") in factors
    # eight factors for AAPL
    assert result.computed["US_EQ_AAPL"] == 8

    with get_connection(db_path) as conn:
        snaps = conn.execute(
            "SELECT asset_id, intrinsic_value_per_share, wacc FROM valuation_snapshots ORDER BY asset_id"
        ).fetchall()
    assert [s[0] for s in snaps] == ["US_EQ_AAPL", "US_EQ_MSFT"]
    # WACC = Rf(0.045 default) + beta(1.0 fallback) * 5.5% = 0.10
    assert all(abs(s[2] - 0.10) < 1e-9 for s in snaps)
    assert all(s[1] > 0 for s in snaps)

    # NVDA has a price but no fundamentals: no crash, no multiples, DCF skipped.
    assert result.computed["US_EQ_NVDA"] == 0
    assert "US_EQ_NVDA" in result.dcf_skipped


def test_daily_run_multiples_without_dcf(tmp_path: Path) -> None:
    db_path = tmp_path / "v.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed(conn)
        compute_and_store_valuation_factors(conn, include_dcf=False, as_of=AS_OF)
        names = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT factor_name FROM factor_values WHERE date = ?", [AS_OF]
            ).fetchall()
        }
        snaps = conn.execute("SELECT COUNT(*) FROM valuation_snapshots").fetchone()[0]

    # Multiples + percentiles computed daily; no DCF / price_to_intrinsic.
    assert "pe_ratio" in names and "pe_vs_sector_pct" in names
    assert "price_to_intrinsic" not in names
    assert snaps == 0
