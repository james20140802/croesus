from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

YFINANCE_TICKERS = [
    "^VIX",      # CBOE VIX
    "^VIX3M",    # 3-Month VIX
    "^GSPC",     # S&P 500
    "DX-Y.NYB",  # DXY Dollar Index
    "KRW=X",     # USD/KRW
    "HG=F",      # Copper Futures
    "GC=F",      # Gold Futures
    "CL=F",      # WTI Crude Futures
]


class YFinanceMacroSource:
    """Fetch macro market data from yfinance (no API key required)."""

    def fetch(
        self,
        tickers: list[str] | None = None,
        lookback_years: int = 5,
    ) -> dict[str, pd.Series]:
        """
        Return dict {ticker: pd.Series of adjusted close prices}.

        Failed tickers are logged and skipped.
        """
        tickers = tickers or YFINANCE_TICKERS
        period = f"{lookback_years}y"
        result: dict[str, pd.Series] = {}

        for ticker in tickers:
            try:
                df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
                if df is None or df.empty:
                    logger.warning("yfinance %s: empty result", ticker)
                    continue
                close = df["Close"].squeeze()
                if isinstance(close, pd.DataFrame):
                    close = close.iloc[:, 0]
                close = close.dropna().astype(float)
                close.name = ticker
                result[ticker] = close
                logger.debug("yfinance %s: %d rows", ticker, len(close))
            except Exception as exc:
                logger.warning("yfinance %s failed: %s", ticker, exc)

        return result
