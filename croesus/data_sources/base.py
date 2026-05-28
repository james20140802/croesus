from __future__ import annotations

from typing import Protocol

import pandas as pd


class DailyPriceSource(Protocol):
    def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
        """Return daily OHLCV rows with normalized Croesus column names."""
