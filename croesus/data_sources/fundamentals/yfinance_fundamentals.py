from __future__ import annotations

import pandas as pd
import yfinance as yf

from croesus.data_sources.fundamentals.base import Financials


class YFinanceFundamentalsProvider:
    """yfinance-backed :class:`FundamentalsProvider`.

    Returns the income/balance/cashflow statements verbatim (period-end columns,
    yfinance line-item rows). Statement-level failures degrade to empty frames so
    one bad statement never sinks the whole symbol; per-symbol failures are the
    ingestion loop's responsibility.
    """

    source_name = "yfinance"

    def get_financials(self, symbol: str) -> Financials:
        ticker = yf.Ticker(symbol)
        return Financials(
            income_annual=self._frame(ticker, "financials"),
            income_quarterly=self._frame(ticker, "quarterly_financials"),
            balance_annual=self._frame(ticker, "balance_sheet"),
            cashflow_annual=self._frame(ticker, "cashflow"),
        )

    @staticmethod
    def _frame(ticker: yf.Ticker, attr: str) -> pd.DataFrame:
        try:
            frame = getattr(ticker, attr)
        except Exception:  # noqa: BLE001 - missing statement is not fatal.
            return pd.DataFrame()
        if frame is None or not isinstance(frame, pd.DataFrame):
            return pd.DataFrame()
        return frame
