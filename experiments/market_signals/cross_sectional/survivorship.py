"""Survivorship-bias sensitivity — pure helpers.

We cannot obtain delisted-stock prices (yfinance purges them), so we cannot build
a truly survivorship-free universe. Instead we *bound* the bias: inject realistic
for-cause delistings into the survivor panel, concentrated on fragile names (high
volatility / low liquidity — the segment that actually delists most), give them a
large terminal loss, and re-measure whether the high-risk (vol/beta) premium and
the small/illiquid premium survive.

This is a MODEL of the bias, parameterised by an assumed annual for-cause delisting
rate and a fragility tilt — not real data. The deliverable is a sensitivity curve:
at what assumed delisting rate does each premium vanish, vs the realistic ~2-4%/yr?
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def to_percentile(s: pd.Series) -> pd.Series:
    """Cross-sectional rank in [0, 1] (0 = lowest, 1 = highest). NaNs stay NaN."""
    v = s.dropna()
    if len(v) <= 1:
        return pd.Series(0.5, index=v.index)
    r = v.rank(method="average")
    return (r - 1) / (len(v) - 1)


def fragility_percentile(vol: pd.Series, liq: pd.Series) -> pd.Series:
    """Delisting-risk proxy in [0,1]: high volatility and/or low liquidity = fragile.

    Averages the volatility percentile with the *inverse* liquidity percentile over
    the names that have both; falls back to whichever is available.
    """
    vp, lp = to_percentile(vol), to_percentile(liq)
    idx = vp.index.union(lp.index)
    parts = []
    if len(vp):
        parts.append(vp.reindex(idx))
    if len(lp):
        parts.append((1.0 - lp).reindex(idx))
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts, axis=1).mean(axis=1)


def hazard_prob(frag_pct: pd.Series, base_monthly: float, k: float) -> pd.Series:
    """Per-name monthly delisting probability = base * exp(k*(frag-0.5)), clipped.

    ``k=0`` → uniform hazard (=base for everyone); larger ``k`` concentrates hazard
    on fragile names. Result clipped to [0, 1].
    """
    p = base_monthly * np.exp(k * (frag_pct - 0.5))
    return p.clip(lower=0.0, upper=1.0)


def draw_terminal_returns(mask_index, rng: np.random.Generator,
                          lo: float = -1.0, hi: float = -0.5) -> pd.Series:
    """Terminal (final-period) return for for-cause delistings ~ Uniform(lo, hi)."""
    n = len(mask_index)
    return pd.Series(rng.uniform(lo, hi, size=n), index=mask_index)
