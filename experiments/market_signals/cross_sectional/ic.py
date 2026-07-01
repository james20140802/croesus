"""Spearman Information Coefficient and its time-series summary."""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from experiments.market_signals.cross_sectional.stats import newey_west_se


def spearman_ic(values: pd.Series, fwd: pd.Series) -> float:
    """Rank correlation of a signal cross-section with forward returns."""
    df = pd.concat([values.rename("v"), fwd.rename("f")], axis=1).dropna()
    if len(df) < 5 or df["v"].nunique() < 2 or df["f"].nunique() < 2:
        return float("nan")
    return float(spearmanr(df["v"], df["f"]).correlation)


def summarize_ic(ic_series: pd.Series) -> dict:
    """Mean IC, dispersion, Newey-West t-stat, IC IR and hit rate over time."""
    s = pd.to_numeric(ic_series, errors="coerce").dropna()
    if len(s) == 0:
        return {"mean": float("nan"), "std": float("nan"), "t_nw": float("nan"),
                "ir": float("nan"), "hit_rate": float("nan"), "n": 0}
    mean = float(s.mean())
    std = float(s.std(ddof=1)) if len(s) > 1 else float("nan")
    se = newey_west_se(s.to_numpy())
    t_nw = float(mean / se) if se and np.isfinite(se) and se > 0 else float("nan")
    ir = float(mean / std) if std and std > 0 else float("nan")
    return {"mean": mean, "std": std, "t_nw": t_nw, "ir": ir,
            "hit_rate": float((s > 0).mean()), "n": int(len(s))}


def ic_decay(panel: pd.DataFrame, factor: str, horizons) -> dict:
    """Mean IC per horizon for one factor (IC-decay curve)."""
    out: dict[int, float] = {}
    for h in horizons:
        col = f"fwd_{h}"
        rows = panel[(panel["factor_name"] == factor) & panel[col].notna()]
        ics = rows.groupby("date").apply(lambda g: spearman_ic(g["value"], g[col]))
        out[h] = float(pd.to_numeric(ics, errors="coerce").dropna().mean())
    return out
