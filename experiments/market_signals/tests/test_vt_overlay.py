import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.overlay import overlay_returns, target_exposure


def test_target_exposure_rule():
    assert abs(target_exposure(0.30, 0.15, 1.5) - 0.5) < 1e-12
    assert target_exposure(0.05, 0.15, 1.5) == 1.5          # capped
    assert target_exposure(float("nan"), 0.15, 1.5) == 1.0  # fallback


def test_overlay_applies_next_day_no_lookahead():
    idx = pd.bdate_range("2021-01-01", periods=6)
    r = pd.Series([0.01, 0.02, -0.01, 0.03, 0.01, -0.02], index=idx)
    e = pd.Series({idx[1]: 0.5})           # set at day1 close
    out = overlay_returns(r, e)
    assert out.index[0] == idx[2]          # effective from day2
    assert abs(out.loc[idx[2]] - 0.5 * -0.01) < 1e-12


def test_overlay_cost_charged_on_exposure_change():
    idx = pd.bdate_range("2021-01-01", periods=8)
    r = pd.Series(0.0, index=idx)
    e = pd.Series({idx[0]: 1.0, idx[3]: 0.5})
    out = overlay_returns(r, e, cost_bps=10.0)
    # day4: |Δw|=0.5 → cost 0.5 * 10bp = 5bp
    assert abs(out.loc[idx[4]] + 0.5 * 0.0010) < 1e-12
    assert abs(out.loc[idx[2]]) < 1e-12    # no change, no cost


def test_overlay_constant_full_exposure_equals_underlying():
    idx = pd.bdate_range("2021-01-01", periods=5)
    r = pd.Series([0.01, -0.01, 0.02, 0.0, 0.01], index=idx)
    e = pd.Series({idx[0]: 1.0})
    out = overlay_returns(r, e)
    assert np.allclose(out.values, r.iloc[1:].values)
