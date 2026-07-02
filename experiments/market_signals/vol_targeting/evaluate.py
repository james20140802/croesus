"""Forecast-accuracy metrics: MSE, QLIKE, and a DM-style HAC t-test on loss diffs."""
from __future__ import annotations

import numpy as np

from experiments.market_signals.cross_sectional.stats import newey_west_se


def mse_loss(forecast_vol, realized_vol) -> np.ndarray:
    f = np.asarray(forecast_vol, dtype=float)
    r = np.asarray(realized_vol, dtype=float)
    return (f - r) ** 2


def qlike_loss(forecast_vol, realized_vol) -> np.ndarray:
    """QLIKE on variances: rv/f - log(rv/f) - 1 (0 at f=rv, robust to noisy rv)."""
    f2 = np.asarray(forecast_vol, dtype=float) ** 2
    r2 = np.asarray(realized_vol, dtype=float) ** 2
    ratio = r2 / f2
    return ratio - np.log(ratio) - 1.0


def loss_diff_tstat(loss_a, loss_b, lags: "int | None" = None) -> dict:
    """Diebold-Mariano-style test on mean(loss_a - loss_b); negative t => a better."""
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    d = d[np.isfinite(d)]
    mean = float(d.mean()) if len(d) else float("nan")
    se = newey_west_se(d, lags)
    t = mean / se if np.isfinite(se) and se > 0 else float("nan")
    return {"mean_diff": mean, "t": float(t), "n": int(len(d))}
