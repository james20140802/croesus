import numpy as np
import pandas as pd

from experiments.market_signals.cross_sectional.portfolio import (
    long_short_return,
    perf_summary,
    quintile_buckets,
    turnover,
)


def test_quintile_buckets_range():
    v = pd.Series(np.arange(50, dtype=float))
    b = quintile_buckets(v, 5)
    assert set(b.unique()) <= {1, 2, 3, 4, 5} and b.notna().all()


def test_long_short_monotone_positive():
    v = pd.Series(np.arange(50, dtype=float))
    f = pd.Series(np.arange(50, dtype=float))
    assert long_short_return(v, f, 5) > 0


def test_turnover_bounds():
    assert turnover({"a", "b"}, {"a", "b"}) == 0.0
    assert turnover({"a", "b"}, {"c", "d"}) == 1.0


def test_perf_summary_keys_and_maxdd_sign():
    r = pd.Series([0.01, -0.02, 0.03, 0.00, 0.015])
    out = perf_summary(r, 12)
    assert {"cum", "sharpe", "mean", "vol", "maxdd"} <= set(out)
    assert out["maxdd"] <= 0
