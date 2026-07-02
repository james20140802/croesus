import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.forecasters import ewma_forecast, naive_forecast
from experiments.market_signals.vol_targeting.realized import TRADING_DAYS


def _returns(n=500, scale=0.01, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    return pd.Series(rng.normal(0, scale, n), index=idx)


def test_naive_is_trailing_realized_vol():
    r = _returns()
    expected = r.iloc[-21:].std(ddof=1) * np.sqrt(TRADING_DAYS)
    assert abs(naive_forecast(r) - expected) < 1e-12
    assert np.isnan(naive_forecast(r.iloc[:10]))


def test_ewma_near_true_vol_on_iid_series():
    r = _returns(n=2000, scale=0.01)
    f = ewma_forecast(r)
    true_ann = 0.01 * np.sqrt(TRADING_DAYS)
    assert 0.7 * true_ann < f < 1.3 * true_ann
    assert np.isnan(ewma_forecast(r.iloc[:20]))


def test_ewma_reacts_to_recent_vol_jump():
    calm = _returns(n=400, scale=0.005, seed=2)
    idx2 = pd.bdate_range(calm.index[-1] + pd.Timedelta(days=1), periods=60)
    wild = pd.Series(np.random.default_rng(3).normal(0, 0.03, 60), index=idx2)
    f_calm = ewma_forecast(calm)
    f_after = ewma_forecast(pd.concat([calm, wild]))
    assert f_after > 2 * f_calm
