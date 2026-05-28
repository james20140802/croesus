from __future__ import annotations

from typing import Any

import duckdb
import pandas as pd


class PriceRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_daily_prices(self, asset_id: str, prices: pd.DataFrame, *, source: str) -> int:
        expected = ["date", "open", "high", "low", "close", "adjusted_close", "volume"]
        missing = [column for column in expected if column not in prices.columns]
        if missing:
            raise ValueError(f"price frame missing columns: {', '.join(missing)}")
        if prices.empty:
            return 0

        rows = [
            (
                asset_id,
                pd.Timestamp(row.date).date(),
                self._optional_float(row.open),
                self._optional_float(row.high),
                self._optional_float(row.low),
                self._optional_float(row.close),
                self._optional_float(row.adjusted_close),
                self._optional_int(row.volume),
                source,
            )
            for row in prices[expected].itertuples(index=False)
        ]
        self.conn.executemany(
            """
            INSERT INTO prices_daily (
              asset_id, date, open, high, low, close, adjusted_close, volume, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, date) DO UPDATE SET
              open = excluded.open,
              high = excluded.high,
              low = excluded.low,
              close = excluded.close,
              adjusted_close = excluded.adjusted_close,
              volume = excluded.volume,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def load_daily_prices(self, asset_id: str) -> pd.DataFrame:
        return self.conn.execute(
            """
            SELECT date, open, high, low, close, adjusted_close, volume, source
            FROM prices_daily
            WHERE asset_id = ?
            ORDER BY date
            """,
            [asset_id],
        ).df()

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if pd.isna(value):
            return None
        return int(value)
