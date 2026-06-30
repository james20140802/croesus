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
            cashflow_annual=self._get_yearly(ticker, "cashflow"),
        )

    def _get_yearly(self, ticker: yf.Ticker, kind: str) -> pd.DataFrame:
        """Annual statement with the widest history yfinance offers.

        `ticker.cashflow` returns ~4 columns; `get_cashflow(freq="yearly")`
        returns up to ~10 when available. Falls back to the attribute if the
        method is absent or raises (older yfinance / offline).
        """
        getter = getattr(ticker, f"get_{kind}", None)
        if getter is not None:
            try:
                frame = getter(freq="yearly")
                if frame is not None and not frame.empty:
                    return frame
            except Exception:  # noqa: BLE001 - fall back to the attribute below
                pass
        return self._frame(ticker, kind)

    @staticmethod
    def _frame(ticker: yf.Ticker, attr: str) -> pd.DataFrame:
        try:
            frame = getattr(ticker, attr)
        except Exception:  # noqa: BLE001 - missing statement is not fatal.
            return pd.DataFrame()
        if frame is None or not isinstance(frame, pd.DataFrame):
            return pd.DataFrame()
        return frame
