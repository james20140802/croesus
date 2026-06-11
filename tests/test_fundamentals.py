from datetime import date
from pathlib import Path

import pandas as pd

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.fundamentals.ingest_fundamentals import ingest_fundamentals
from croesus.fundamentals.repository import (
    METRIC_BOOK_VALUE_PER_SHARE,
    METRIC_FREE_CASH_FLOW,
    PERIOD_ANNUAL,
    FundamentalMetric,
    FundamentalsRepository,
)

_COLS = [pd.Timestamp("2023-12-31"), pd.Timestamp("2024-12-31")]


def _income_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            _COLS[0]: [100.0, 30.0, 20.0, 1.0, 40.0],
            _COLS[1]: [120.0, 36.0, 24.0, 1.2, 48.0],
        },
        index=["Total Revenue", "Operating Income", "Net Income", "Diluted EPS", "EBITDA"],
    )


def _balance_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            _COLS[0]: [50.0, 200.0, 30.0, 10.0],
            _COLS[1]: [55.0, 240.0, 35.0, 10.0],
        },
        index=[
            "Total Debt",
            "Stockholders Equity",
            "Cash And Cash Equivalents",
            "Ordinary Shares Number",
        ],
    )


def _cashflow_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {_COLS[0]: [18.0, -8.0], _COLS[1]: [22.0, -9.0]},
        index=["Free Cash Flow", "Capital Expenditure"],
    )


class FakeFundamentalsProvider:
    source_name = "fake"

    def __init__(self, failing_symbol: str | None = None, empty_symbol: str | None = None) -> None:
        self.failing_symbol = failing_symbol
        self.empty_symbol = empty_symbol

    def get_financials(self, symbol: str) -> dict:
        if symbol == self.failing_symbol:
            raise RuntimeError("provider unavailable")
        if symbol == self.empty_symbol:
            return {
                "income_annual": pd.DataFrame(),
                "income_quarterly": pd.DataFrame(),
                "balance_annual": pd.DataFrame(),
                "cashflow_annual": pd.DataFrame(),
            }
        return {
            "income_annual": _income_frame(),
            "income_quarterly": pd.DataFrame(),
            "balance_annual": _balance_frame(),
            "cashflow_annual": _cashflow_frame(),
        }


def test_repository_upsert_and_query(tmp_path: Path) -> None:
    db_path = tmp_path / "f.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        repo = FundamentalsRepository(conn)
        repo.upsert_metrics(
            [
                FundamentalMetric("US_EQ_AAPL", date(2023, 12, 31), PERIOD_ANNUAL, METRIC_FREE_CASH_FLOW, 18.0, "t"),
                FundamentalMetric("US_EQ_AAPL", date(2024, 12, 31), PERIOD_ANNUAL, METRIC_FREE_CASH_FLOW, 22.0, "t"),
            ]
        )
        # idempotent update
        repo.upsert_metrics(
            [FundamentalMetric("US_EQ_AAPL", date(2024, 12, 31), PERIOD_ANNUAL, METRIC_FREE_CASH_FLOW, 25.0, "t")]
        )

        assert repo.get_annual_fcf("US_EQ_AAPL") == [
            (date(2023, 12, 31), 18.0),
            (date(2024, 12, 31), 25.0),
        ]
        assert repo.get_latest_metric("US_EQ_AAPL", METRIC_FREE_CASH_FLOW) == 25.0
        assert repo.get_latest_metric("US_EQ_AAPL", "missing") is None


def test_ingest_maps_labels_derives_bvps_and_continues_after_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "f.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        provider = FakeFundamentalsProvider(failing_symbol="MSFT", empty_symbol="NVDA")
        result = ingest_fundamentals(conn, provider)

        repo = FundamentalsRepository(conn)
        # AAPL succeeds: labels mapped to metric_name vocabulary.
        assert "AAPL" in result.succeeded
        assert result.failed == {"MSFT": "provider unavailable"}
        assert result.skipped == {"NVDA": "no fundamentals returned"}

        assert repo.get_latest_metric("US_EQ_AAPL", "revenue") == 120.0
        assert repo.get_latest_metric("US_EQ_AAPL", "eps") == 1.2
        assert repo.get_latest_metric("US_EQ_AAPL", "ebitda") == 48.0
        assert repo.get_annual_fcf("US_EQ_AAPL")[-1] == (date(2024, 12, 31), 22.0)
        # book_value_per_share derived = total_equity / shares_outstanding.
        assert repo.get_latest_metric("US_EQ_AAPL", METRIC_BOOK_VALUE_PER_SHARE) == 24.0

        # nothing stored for the failed/empty symbols
        assert repo.get_annual_fcf("US_EQ_MSFT") == []
        assert repo.get_annual_fcf("US_EQ_NVDA") == []
