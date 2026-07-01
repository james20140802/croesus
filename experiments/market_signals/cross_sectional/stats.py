"""HAC (Newey-West) standard error for a mean, and a permutation IC null."""
from __future__ import annotations

import numpy as np


def newey_west_se(x, lags: "int | None" = None) -> float:
    """HAC standard error of the sample mean of a (serially correlated) series."""
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return float("nan")
    if lags is None:
        lags = int(np.floor(4 * (n / 100.0) ** (2 / 9)))
    e = x - x.mean()
    gamma0 = float((e * e).sum()) / n
    s = gamma0
    for lag in range(1, lags + 1):
        if lag >= n:
            break
        w = 1.0 - lag / (lags + 1)
        cov = float((e[lag:] * e[:-lag]).sum()) / n
        s += 2 * w * cov
    return float(np.sqrt(max(s, 0.0) / n))


def permutation_ic_null(values, fwd, n: int = 1000, seed: int = 0) -> np.ndarray:
    """Null distribution of a single cross-section's Spearman IC under label shuffling."""
    from scipy.stats import spearmanr

    v = np.asarray(values, dtype=float)
    f = np.asarray(fwd, dtype=float)
    m = np.isfinite(v) & np.isfinite(f)
    v, f = v[m], f[m]
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    if len(v) < 5:
        out[:] = np.nan
        return out
    for i in range(n):
        out[i] = spearmanr(rng.permutation(v), f).correlation
    return out
