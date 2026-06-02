from __future__ import annotations

from datetime import date
from typing import Any

import duckdb
import pandas as pd


class FxRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_rates(
        self,
        quote_currency: str,
        rates: pd.DataFrame,
        *,
        source: str,
    ) -> int:
        expected = ["date", "rate_per_usd"]
        missing = [column for column in expected if column not in rates.columns]
        if missing:
            raise ValueError(f"fx frame missing columns: {', '.join(missing)}")
        if rates.empty:
            return 0

        quote = quote_currency.upper()
        rows = [
            (
                quote,
                pd.Timestamp(row.date).date(),
                self._optional_float(row.rate_per_usd),
                source,
            )
            for row in rates[expected].itertuples(index=False)
        ]
        self.conn.executemany(
            """
            INSERT INTO fx_rates (quote_currency, date, rate_per_usd, source)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (quote_currency, date) DO UPDATE SET
              rate_per_usd = excluded.rate_per_usd,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def get_latest_rate(self, quote_currency: str, as_of: date) -> float | None:
        quote = quote_currency.upper()
        if quote == "USD":
            return 1.0
        row = self.conn.execute(
            """
            SELECT rate_per_usd
            FROM fx_rates
            WHERE quote_currency = ? AND date <= ?
            ORDER BY date DESC
            LIMIT 1
            """,
            [quote, as_of],
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if pd.isna(value):
            return None
        return float(value)
