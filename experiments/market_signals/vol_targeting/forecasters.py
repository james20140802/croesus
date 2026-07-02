"""Volatility forecasters: naive (trailing RV), EWMA (RiskMetrics), GARCH(1,1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.realized import TRADING_DAYS


def naive_forecast(returns: pd.Series, window: int = 21) -> float:
    """Forecast = trailing realized vol (annualized). The baseline to beat."""
    r = returns.dropna()
    if len(r) < window:
        return float("nan")
    return float(r.iloc[-window:].std(ddof=1) * np.sqrt(TRADING_DAYS))


def ewma_forecast(returns: pd.Series, lam: float = 0.94) -> float:
    """RiskMetrics EWMA variance recursion, annualized vol forecast."""
    r = returns.dropna().to_numpy(dtype=float)
    if len(r) < 30:
        return float("nan")
    var = float(np.mean(r[:30] ** 2))  # seed with the first month's mean square
    for x in r[30:]:
        var = lam * var + (1 - lam) * x * x
    return float(np.sqrt(var * TRADING_DAYS))
