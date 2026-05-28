from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.compute_common_factors import compute_and_store_common_factors
from croesus.prices.ingest_prices import ingest_daily_prices
from croesus.prices.repository import PriceRepository


class FakePriceSource:
    def __init__(self, failing_symbol: str | None = None) -> None:
        self.failing_symbol = failing_symbol

    def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        if symbol == self.failing_symbol:
            raise RuntimeError("source unavailable")
        return pd.DataFrame(
            [
                {
                    "date": date(2026, 1, 2),
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "adjusted_close": 10.4,
                    "volume": 1000,
                }
            ]
        )


def test_ingest_daily_prices_reads_active_assets_and_continues_after_failure(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        result = ingest_daily_prices(conn, FakePriceSource(failing_symbol="MSFT"))
        stored = conn.execute(
            "SELECT asset_id, close, source FROM prices_daily ORDER BY asset_id"
        ).fetchall()

    assert result.succeeded == ["AAPL", "NVDA"]
    assert result.failed == {"MSFT": "source unavailable"}
    assert stored == [
        ("US_EQ_AAPL", 10.5, "yfinance"),
        ("US_EQ_NVDA", 10.5, "yfinance"),
    ]


def test_compute_common_factors_stores_latest_values_and_skips_short_history(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    start = date(2025, 1, 1)
    long_history = pd.DataFrame(
        {
            "date": [start + timedelta(days=offset) for offset in range(230)],
            "open": [100.0 + offset for offset in range(230)],
            "high": [101.0 + offset for offset in range(230)],
            "low": [99.0 + offset for offset in range(230)],
            "close": [100.0 + offset for offset in range(230)],
            "adjusted_close": [100.0 + offset for offset in range(230)],
            "volume": [1_000 + offset for offset in range(230)],
        }
    )
    short_history = long_history.head(20)

    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        prices = PriceRepository(conn)
        prices.upsert_daily_prices("US_EQ_AAPL", long_history, source="test")
        prices.upsert_daily_prices("US_EQ_MSFT", short_history, source="test")

        result = compute_and_store_common_factors(conn)
        rows = conn.execute(
            """
            SELECT asset_id, factor_name, value
            FROM factor_values
            ORDER BY asset_id, factor_name
            """
        ).fetchall()

    assert result.computed == {"US_EQ_AAPL": 6}
    assert "US_EQ_MSFT" in result.skipped
    assert {row[1] for row in rows} == {
        "above_200d_ma",
        "liquidity_1m",
        "momentum_1m",
        "momentum_3m",
        "momentum_6m",
        "volatility_3m",
    }
    assert all(row[0] == "US_EQ_AAPL" for row in rows)
