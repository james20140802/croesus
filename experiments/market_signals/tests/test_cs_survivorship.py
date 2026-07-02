import numpy as np
import pandas as pd

from experiments.market_signals.cross_sectional.survivorship import (
    draw_terminal_returns,
    fragility_percentile,
    hazard_prob,
    to_percentile,
)


def test_to_percentile_bounds_and_order():
    s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    p = to_percentile(s)
    assert abs(p.min() - 0.0) < 1e-9 and abs(p.max() - 1.0) < 1e-9
    assert p.iloc[0] < p.iloc[-1]


def test_fragility_high_vol_low_liq_is_fragile():
    vol = pd.Series({"a": 0.5, "b": 0.1})   # a = high vol
    liq = pd.Series({"a": 1.0, "b": 9.0})   # a = low liquidity
    frag = fragility_percentile(vol, liq)
    assert frag["a"] > frag["b"]            # a fragile on both axes


def test_hazard_prob_monotone_and_clipped():
    frag = pd.Series([0.0, 0.5, 1.0])
    p = hazard_prob(frag, base_monthly=0.005, k=2.0)
    assert p.iloc[0] < p.iloc[1] < p.iloc[2]
    assert (p >= 0).all() and (p <= 1).all()


def test_hazard_k_zero_is_uniform():
    frag = pd.Series([0.0, 0.5, 1.0])
    p = hazard_prob(frag, base_monthly=0.01, k=0.0)
    assert np.allclose(p.values, 0.01)


def test_terminal_returns_in_range_and_seeded():
    idx = pd.Index(["x", "y", "z"])
    r1 = draw_terminal_returns(idx, np.random.default_rng(0))
    r2 = draw_terminal_returns(idx, np.random.default_rng(0))
    assert ((r1 >= -1.0) & (r1 <= -0.5)).all()
    assert np.allclose(r1.values, r2.values)  # deterministic under same seed
