import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.realized import (
    TRADING_DAYS,
    daily_returns,
    forward_realized_vol,
    realized_vol,
)


def _series(n=100, scale=0.01, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(100 * np.cumprod(1 + rng.normal(0, scale, n)), index=idx)


def test_daily_returns_matches_pct_change():
    close = _series()
    r = daily_returns(close)
    assert len(r) == len(close) - 1
    assert abs(r.iloc[0] - (close.iloc[1] / close.iloc[0] - 1)) < 1e-12


def test_realized_vol_annualized():
    r = daily_returns(_series(300, scale=0.01))
    rv = realized_vol(r, window=21)
    assert rv.notna().sum() == len(r) - 20
    last = r.iloc[-21:].std(ddof=1) * np.sqrt(TRADING_DAYS)
    assert abs(rv.iloc[-1] - last) < 1e-12


def test_forward_realized_vol_excludes_asof_and_needs_full_window():
    r = daily_returns(_series(60))
    as_of = r.index[30]
    expected = r.iloc[31:52].std(ddof=1) * np.sqrt(TRADING_DAYS)
    assert abs(forward_realized_vol(r, as_of, 21) - expected) < 1e-12
    assert np.isnan(forward_realized_vol(r, r.index[-5], 21))
