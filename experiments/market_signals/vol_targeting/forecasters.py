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


def _garch11_neg_loglik(params: np.ndarray, r: np.ndarray) -> float:
    omega, alpha, beta = params
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.999:
        return 1e10
    var = np.empty(len(r))
    var[0] = np.var(r)
    for t in range(1, len(r)):
        var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
    var = np.maximum(var, 1e-12)
    return float(0.5 * np.sum(np.log(var) + r ** 2 / var))


def fit_garch11(returns: pd.Series) -> dict:
    """Gaussian MLE of GARCH(1,1) on demeaned daily returns (scipy Nelder-Mead)."""
    from scipy.optimize import minimize

    r = returns.dropna().to_numpy(dtype=float)
    r = r - r.mean()
    v = float(np.var(r))
    x0 = np.array([0.05 * v, 0.08, 0.90])
    res = minimize(_garch11_neg_loglik, x0, args=(r,), method="Nelder-Mead",
                   options={"maxiter": 2000, "xatol": 1e-12, "fatol": 1e-9})
    omega, alpha, beta = (float(p) for p in res.x)
    var = np.empty(len(r))
    var[0] = v
    for t in range(1, len(r)):
        var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
    next_var = max(omega + alpha * r[-1] ** 2 + beta * var[-1], 1e-12)
    return {"omega": omega, "alpha": alpha, "beta": beta,
            "next_var": float(next_var), "converged": bool(res.success)}


def garch11_forecast(returns: pd.Series, horizon: int = 21) -> float:
    """Annualized vol from the mean of the next-`horizon` daily conditional variances."""
    r = returns.dropna()
    if len(r) < 250:
        return float("nan")
    fit = fit_garch11(r)
    omega, a, b = fit["omega"], fit["alpha"], fit["beta"]
    persistence = a + b
    if persistence >= 0.999:
        mean_var = fit["next_var"]
    else:
        uncond = omega / (1 - persistence)
        mean_var = float(np.mean(
            [uncond + persistence ** k * (fit["next_var"] - uncond) for k in range(horizon)]
        ))
    return float(np.sqrt(max(mean_var, 1e-12) * TRADING_DAYS))
