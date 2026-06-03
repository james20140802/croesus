from datetime import date
from pathlib import Path

import pandas as pd

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.fx.convert import to_base
from croesus.fx.ingest_fx_rates import ingest_fx_rates
from croesus.fx.repository import FxRepository


class FakeFxSource:
    def __init__(self, failing_symbol: str | None = None) -> None:
        self.failing_symbol = failing_symbol

    def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        if symbol == self.failing_symbol:
            raise RuntimeError("fx unavailable")
        return pd.DataFrame(
            [
                {
                    "date": date(2026, 5, 29),
                    "open": 1490.0,
                    "high": 1510.0,
                    "low": 1480.0,
                    "close": 1500.0,
                    "adjusted_close": 1500.0,
                    "volume": 0,
                },
                {
                    "date": date(2026, 6, 1),
                    "open": 1505.0,
                    "high": 1515.0,
                    "low": 1495.0,
                    "close": 1507.5,
                    "adjusted_close": 1507.5,
                    "volume": 0,
                },
            ]
        )


def test_migrate_creates_fx_rates_table(tmp_path: Path) -> None:
    db_path = tmp_path / "fx.duckdb"

    migrate(db_path)

    with get_connection(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

    assert "fx_rates" in tables


def test_fx_repository_returns_latest_rate_at_or_before_date(tmp_path: Path) -> None:
    db_path = tmp_path / "fx.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = FxRepository(conn)
        repo.upsert_rates(
            "KRW",
            pd.DataFrame(
                [
                    {"date": date(2026, 5, 29), "rate_per_usd": 1500.0},
                    {"date": date(2026, 6, 1), "rate_per_usd": 1507.5},
                ]
            ),
            source="test",
        )

        assert repo.get_latest_rate("KRW", date(2026, 5, 30)) == 1500.0
        assert repo.get_latest_rate("KRW", date(2026, 6, 2)) == 1507.5
        assert repo.get_latest_rate("USD", date(2026, 6, 2)) == 1.0
        assert repo.get_latest_rate("EUR", date(2026, 6, 2)) is None


def test_to_base_converts_through_rate_per_usd() -> None:
    rates = {"USD": 1.0, "KRW": 1500.0, "EUR": 0.8}

    assert to_base(150_000.0, native_currency="KRW", base_currency="USD", rates=rates) == 100.0
    assert to_base(100.0, native_currency="USD", base_currency="KRW", rates=rates) == 150_000.0
    assert to_base(80.0, native_currency="EUR", base_currency="USD", rates=rates) == 100.0


def test_ingest_fx_rates_reads_requested_currencies_and_continues_after_failure(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fx.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        result = ingest_fx_rates(
            conn,
            currencies=["USD", "KRW", "EUR"],
            source=FakeFxSource(failing_symbol="EUR=X"),
            log=lambda m: None,
        )
        rows = conn.execute(
            """
            SELECT quote_currency, date, rate_per_usd, source
            FROM fx_rates
            ORDER BY quote_currency, date
            """
        ).fetchall()

    assert result.succeeded == ["KRW"]
    assert result.failed == {"EUR": "fx unavailable"}
    assert result.skipped == {}
    assert rows == [
        ("KRW", date(2026, 5, 29), 1500.0, "yfinance"),
        ("KRW", date(2026, 6, 1), 1507.5, "yfinance"),
    ]
