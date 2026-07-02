"""Realized volatility helpers (close-to-close, annualized)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252.0


def daily_returns(close: pd.Series) -> pd.Series:
    """Simple daily returns from a close series (index=date)."""
    return close.sort_index().pct_change().dropna()


def realized_vol(returns: pd.Series, window: int = 21) -> pd.Series:
    """Trailing annualized realized vol at each date (full window required)."""
    return returns.rolling(window).std(ddof=1) * np.sqrt(TRADING_DAYS)


def forward_realized_vol(returns: pd.Series, as_of, horizon: int = 21) -> float:
    """Annualized realized vol over the `horizon` trading days AFTER as_of."""
    r = returns.sort_index()
    pos = r.index.searchsorted(pd.Timestamp(as_of), side="right")
    fwd = r.iloc[pos:pos + horizon]
    if len(fwd) < horizon:
        return float("nan")
    return float(fwd.std(ddof=1) * np.sqrt(TRADING_DAYS))
