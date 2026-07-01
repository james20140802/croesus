import numpy as np
import pandas as pd

from experiments.market_signals.cross_sectional.returns import forward_returns


def test_forward_return_basic():
    idx = pd.bdate_range("2020-01-01", periods=200)
    close = pd.Series(100.0 * (1.001 ** np.arange(200)), index=idx)
    as_of = idx[100]
    fr = forward_returns(close, as_of, [21, 63])
    assert abs(fr[21] - (close.iloc[121] / close.iloc[100] - 1)) < 1e-12
    assert abs(fr[63] - (close.iloc[163] / close.iloc[100] - 1)) < 1e-12


def test_horizon_out_of_range_omitted():
    idx = pd.bdate_range("2020-01-01", periods=110)
    close = pd.Series(1.0 + np.arange(110), index=idx)
    fr = forward_returns(close, idx[100], [21, 63, 126])
    # pos=100, n=110 -> 100+21=121 >= 110, all omitted
    assert fr == {}


def test_missing_asof_returns_empty():
    idx = pd.bdate_range("2020-01-01", periods=50)
    close = pd.Series(1.0, index=idx)
    assert forward_returns(close, pd.Timestamp("1999-01-01"), [21]) == {}
