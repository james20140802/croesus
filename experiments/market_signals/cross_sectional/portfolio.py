"""Quantile long-short portfolio and performance helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd


def quintile_buckets(values: pd.Series, q: int = 5) -> pd.Series:
    """Assign 1..q buckets by rank (safe for ties / small cross-sections)."""
    v = values.dropna()
    if len(v) == 0:
        return pd.Series(dtype=int)
    if v.nunique() < q:
        r = v.rank(method="first")
        return np.ceil(r / len(r) * q).clip(1, q).astype(int)
    try:
        b = pd.qcut(v.rank(method="first"), q, labels=False, duplicates="drop") + 1
    except ValueError:
        r = v.rank(method="first")
        b = np.ceil(r / len(r) * q).clip(1, q)
    return b.astype(int)


def long_short_return(values: pd.Series, fwd: pd.Series, q: int = 5) -> float:
    """Equal-weight top-quantile minus bottom-quantile forward return."""
    df = pd.concat([values.rename("v"), fwd.rename("f")], axis=1).dropna()
    if len(df) < q:
        return float("nan")
    b = quintile_buckets(df["v"], q)
    top = df.loc[b[b == q].index, "f"].mean()
    bot = df.loc[b[b == 1].index, "f"].mean()
    return float(top - bot)


def turnover(prev_top: set, cur_top: set) -> float:
    """Fraction of the current top bucket that is newly entered."""
    if not cur_top:
        return 0.0
    return len(cur_top - prev_top) / len(cur_top)


def perf_summary(returns: pd.Series, periods_per_year: float) -> dict:
    r = pd.to_numeric(returns, errors="coerce").dropna()
    if len(r) == 0:
        return {"cum": 0.0, "sharpe": float("nan"), "mean": float("nan"),
                "vol": float("nan"), "maxdd": 0.0}
    curve = (1 + r).cumprod()
    peak = curve.cummax()
    maxdd = float((curve / peak - 1).min())
    mean = float(r.mean())
    vol = float(r.std(ddof=1)) if len(r) > 1 else float("nan")
    sharpe = float(mean / vol * np.sqrt(periods_per_year)) if vol and vol > 0 else float("nan")
    return {"cum": float(curve.iloc[-1] - 1), "sharpe": sharpe, "mean": mean,
            "vol": vol, "maxdd": maxdd}
