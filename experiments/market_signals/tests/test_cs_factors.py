import numpy as np
import pandas as pd

from experiments.market_signals.cross_sectional.factors import compute_factors_asof


def _hist(n=260, start=100.0, step=1.0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx)
    vol = pd.Series(1000.0, index=idx)
    return pd.DataFrame({"close": close, "volume": vol})


def test_momentum_matches_definition():
    h = _hist()
    f = compute_factors_asof(h, h.index[-1])
    c = h["close"]
    assert abs(f["momentum_1m"] - (c.iloc[-1] / c.iloc[-1 - 21] - 1)) < 1e-12
    assert abs(f["momentum_3m"] - (c.iloc[-1] / c.iloc[-1 - 63] - 1)) < 1e-12
    assert abs(f["momentum_6m"] - (c.iloc[-1] / c.iloc[-1 - 126] - 1)) < 1e-12


def test_above_200d_ma_binary():
    h = _hist()
    f = compute_factors_asof(h, h.index[-1])
    assert f["above_200d_ma"] == 1.0


def test_asof_slices_future_out():
    h = _hist(n=300)
    mid = h.index[250]  # >=200 rows of history so factors are computed
    f = compute_factors_asof(h, mid)
    c = h["close"].loc[:mid]
    assert abs(f["momentum_1m"] - (c.iloc[-1] / c.iloc[-1 - 21] - 1)) < 1e-12


def test_insufficient_history_returns_empty():
    h = _hist(n=50)
    assert compute_factors_asof(h, h.index[-1]) == {}


def test_beta_present_with_market():
    h = _hist()
    mkt = h["close"].pct_change().dropna()
    f = compute_factors_asof(h, h.index[-1], market_ret=mkt)
    assert "beta_1y" in f and np.isfinite(f["beta_1y"])
