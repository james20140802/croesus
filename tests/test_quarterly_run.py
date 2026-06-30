from datetime import date
from pathlib import Path

import pandas as pd

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.quarterly_run import run_quarterly_pipeline
from croesus.prices.repository import PriceRepository

AS_OF = date(2026, 6, 1)
_COLS = [pd.Timestamp("2022-12-31"), pd.Timestamp("2023-12-31"), pd.Timestamp("2024-12-31")]


class FakeProvider:
    source_name = "fake"

    def get_financials(self, symbol: str) -> dict:
        if symbol != "AAPL":  # only AAPL has statements; others are data-deficient
            return {k: pd.DataFrame() for k in
                    ("income_annual", "income_quarterly", "balance_annual", "cashflow_annual")}
        income = pd.DataFrame(
            {c: [100.0, 30.0, 24.0, 5.0, 110.0] for c in _COLS},
            index=["Total Revenue", "Operating Income", "Net Income", "Diluted EPS", "EBITDA"],
        )
        balance = pd.DataFrame(
            {c: [200.0, 250.0, 100.0, 10.0] for c in _COLS},
            index=["Total Debt", "Stockholders Equity", "Cash And Cash Equivalents", "Ordinary Shares Number"],
        )
        cashflow = pd.DataFrame(
            {_COLS[0]: [30.0], _COLS[1]: [40.0], _COLS[2]: [50.0]},
            index=["Free Cash Flow"],
        )
        return {
            "income_annual": income,
            "income_quarterly": pd.DataFrame(),
            "balance_annual": balance,
            "cashflow_annual": cashflow,
        }


def test_quarterly_run_ingests_fundamentals_and_writes_dcf(tmp_path: Path) -> None:
    db_path = tmp_path / "q.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        # quarterly_run seeds assets itself, but needs prices to value them.
        from croesus.assets.seed_us_equities import seed_us_equities

        seed_us_equities(conn)
        PriceRepository(conn).upsert_daily_prices(
            "US_EQ_AAPL",
            pd.DataFrame([
                {"date": AS_OF, "open": 100.0, "high": 100.0, "low": 100.0,
                 "close": 100.0, "adjusted_close": 100.0, "volume": 1000},
            ]),
            source="test",
        )

        result = run_quarterly_pipeline(conn, provider=FakeProvider(), as_of=AS_OF)

        assert "AAPL" in result.fundamentals_result.succeeded
        assert "US_EQ_AAPL" in result.valuation_result.dcf_computed

        fcf = conn.execute(
            "SELECT COUNT(*) FROM fundamentals WHERE metric_name = 'free_cash_flow' AND asset_id = 'US_EQ_AAPL'"
        ).fetchone()[0]
        snap = conn.execute(
            "SELECT intrinsic_value_per_share, wacc FROM valuation_snapshots WHERE asset_id = 'US_EQ_AAPL'"
        ).fetchone()

    assert fcf == 3
    assert snap is not None and snap[0] > 0


def test_quarterly_run_populates_normalized_dcf(tmp_path: Path) -> None:
    db_path = tmp_path / "q_norm.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        from croesus.assets.seed_us_equities import seed_us_equities

        seed_us_equities(conn)
        PriceRepository(conn).upsert_daily_prices(
            "US_EQ_AAPL",
            pd.DataFrame([
                {"date": AS_OF, "open": 100.0, "high": 100.0, "low": 100.0,
                 "close": 100.0, "adjusted_close": 100.0, "volume": 1000},
            ]),
            source="test",
        )

        run_quarterly_pipeline(conn, provider=FakeProvider(), as_of=AS_OF)

        n = conn.execute(
            "SELECT COUNT(*) FROM normalized_dcf_snapshots"
        ).fetchone()[0]
    assert n >= 1
