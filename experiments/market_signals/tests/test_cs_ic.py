import numpy as np
import pandas as pd

from experiments.market_signals.cross_sectional.ic import spearman_ic, summarize_ic


def test_spearman_ic_perfect_monotone():
    v = pd.Series([1, 2, 3, 4, 5])
    f = pd.Series([10, 20, 30, 40, 50])
    assert abs(spearman_ic(v, f) - 1.0) < 1e-9


def test_spearman_ic_inverse():
    v = pd.Series([1, 2, 3, 4, 5])
    f = pd.Series([50, 40, 30, 20, 10])
    assert abs(spearman_ic(v, f) + 1.0) < 1e-9


def test_spearman_ic_nan_dropped():
    # 5 rows survive pairwise dropna (min required), so IC is finite
    v = pd.Series([1, 2, 3, np.nan, 5, 6, 7])
    f = pd.Series([1, 2, np.nan, 4, 5, 6, 7])
    assert np.isfinite(spearman_ic(v, f))


def test_summarize_ic_keys():
    s = pd.Series([0.02, 0.05, -0.01, 0.03, 0.04, 0.0, 0.06])
    out = summarize_ic(s)
    assert {"mean", "std", "t_nw", "ir", "hit_rate", "n"} <= set(out)
    assert out["n"] == 7 and 0 <= out["hit_rate"] <= 1
