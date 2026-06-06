from __future__ import annotations

import pandas as pd
import yfinance as yf


class YFinanceDailyPriceSource:
    source_name = "yfinance"

    def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        raw = yf.download(
            symbol,
            period=period,
            interval="1d",
            auto_adjust=False,
            progress=False,
            threads=False,
        )
        if raw.empty:
            return self._empty_frame()
        return self._normalize(raw)

    @staticmethod
    def _empty_frame() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "adjusted_close", "volume"]
        )

    def _normalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        data = frame.copy()
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        data = data.reset_index()
        data = data.rename(
            columns={
                "Date": "date",
                "Datetime": "date",
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Adj Close": "adjusted_close",
                "Volume": "volume",
            }
        )
        if "adjusted_close" not in data.columns and "close" in data.columns:
            data["adjusted_close"] = data["close"]
        expected = ["date", "open", "high", "low", "close", "adjusted_close", "volume"]
        missing = [column for column in expected if column not in data.columns]
        if missing:
            raise ValueError(f"yfinance response missing columns: {', '.join(missing)}")
        return data[expected]
