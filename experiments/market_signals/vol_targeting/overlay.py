"""Vol-targeting overlay: exposure rule + step-wise overlay returns with costs."""
from __future__ import annotations

import numpy as np
import pandas as pd


def target_exposure(sigma_hat: float, sigma_target: float = 0.15, cap: float = 1.5) -> float:
    """Exposure = min(cap, target/forecast); unusable forecast falls back to 1.0."""
    if not np.isfinite(sigma_hat) or sigma_hat <= 0:
        return 1.0
    return float(min(cap, sigma_target / sigma_hat))


def overlay_returns(daily_ret: pd.Series, exposures: pd.Series,
                    cost_bps: float = 0.0) -> pd.Series:
    """Apply exposures set at rebalance dates, effective the NEXT trading day.

    Cost = |Δexposure| * cost_bps/1e4, charged on the first day the new
    exposure is live. Days before the first exposure are dropped.
    """
    r = daily_ret.sort_index()
    w = pd.Series(np.nan, index=r.index, dtype=float)
    for dt, val in exposures.sort_index().items():
        pos = r.index.searchsorted(pd.Timestamp(dt), side="right")
        if pos < len(r):
            w.iloc[pos] = float(val)
    w = w.ffill()
    dw = w.diff().abs().fillna(0.0)
    out = r * w - dw * (cost_bps / 1e4)
    return out.dropna()
