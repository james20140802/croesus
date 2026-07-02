import numpy as np
import pandas as pd
import pytest

from experiments.market_signals.common import detrend


@pytest.fixture
def price():
    # pure exponential growth: log price is exactly linear
    idx = pd.date_range("2000-01-01", periods=100, freq="D")
    return pd.Series(100 * np.exp(0.001 * np.arange(100)), index=idx)


def test_log_returns_constant_for_exponential(price):
    r = detrend.log_returns(price)
    assert len(r) == len(price) - 1
    assert np.allclose(r.values, 0.001, atol=1e-9)


def test_demean_drift_zero_mean(price):
    r = detrend.demean_drift(detrend.log_returns(price))
    assert abs(r.mean()) < 1e-12


def test_detrend_logprice_linear_removes_trend(price):
    resid = detrend.detrend_logprice(price, kind="linear")
    # exact linear log-price => residual ~ 0
    assert np.allclose(resid.values, 0.0, atol=1e-8)


def test_identity_is_log_price(price):
    out = detrend.identity(price)
    assert np.allclose(out.values, np.log(price.values))


def test_transforms_registry_keys():
    assert set(detrend.TRANSFORMS) == {"identity", "demean_logret", "detrend_linear"}
    # every transform maps a price series to a finite series
    idx = pd.date_range("2000-01-01", periods=50, freq="D")
    p = pd.Series(np.linspace(100, 150, 50), index=idx)
    for name, fn in detrend.TRANSFORMS.items():
        out = fn(p)
        assert np.isfinite(out.values).all()
