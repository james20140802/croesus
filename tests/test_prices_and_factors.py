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


def test_price_repository_returns_latest_close_at_or_before_date(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    prices = pd.DataFrame(
        [
            {
                "date": date(2026, 5, 29),
                "open": 180.0,
                "high": 190.0,
                "low": 179.0,
                "close": 188.0,
                "adjusted_close": 188.0,
                "volume": 1000,
            },
            {
                "date": date(2026, 6, 1),
                "open": 189.0,
                "high": 191.0,
                "low": 187.0,
                "close": 190.0,
                "adjusted_close": 190.0,
                "volume": 1000,
            },
        ]
    )

    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        repo = PriceRepository(conn)
        repo.upsert_daily_prices("US_EQ_AAPL", prices, source="test")

        assert repo.get_latest_close("US_EQ_AAPL", date(2026, 5, 30)) == 188.0
        assert repo.get_latest_close("US_EQ_AAPL", date(2026, 6, 2)) == 190.0
        assert repo.get_latest_close("US_EQ_AAPL", date(2026, 5, 1)) is None


def test_compute_common_factors_adds_beta_when_market_returns_given() -> None:
    # Stock moves exactly 1.5x the market each day → beta must be 1.5.
    from croesus.factors.common import compute_common_factors

    n = 260
    base = date(2024, 1, 1)
    dates = [base + timedelta(days=i) for i in range(n)]
    mkt_ret = [0.01 if i % 2 == 0 else -0.005 for i in range(n)]
    m_close = [100.0]
    s_close = [50.0]
    for i in range(1, n):
        m_close.append(m_close[-1] * (1 + mkt_ret[i]))
        s_close.append(s_close[-1] * (1 + 1.5 * mkt_ret[i]))
    frame = pd.DataFrame({"date": dates, "close": s_close, "volume": [1e6] * n})
    market_returns = {dates[i]: m_close[i] / m_close[i - 1] - 1 for i in range(1, n)}

    with_beta = {
        f.factor_name: f.value
        for f in compute_common_factors("X", frame, market_returns=market_returns)
    }
    assert abs(with_beta["beta_1y"] - 1.5) < 1e-6
    # No market series → beta is simply absent (never crashes, never fabricated).
    without = {f.factor_name for f in compute_common_factors("X", frame)}
    assert "beta_1y" not in without
