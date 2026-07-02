import numpy as np
import pandas as pd

from experiments.market_signals.event_impact import irf


def _series_with_shock(shock_at, drop=-0.10, recover_days=10):
    idx = pd.date_range("2000-01-01", periods=200, freq="B")
    r = pd.Series(0.0, index=idx)
    pos = idx.get_loc(pd.Timestamp(shock_at))
    r.iloc[pos] = drop                      # impulse down
    for k in range(1, recover_days + 1):    # gradual recovery
        r.iloc[pos + k] = -drop / recover_days
    return r


def test_caar_curve_columns_and_peak():
    r = _series_with_shock("2000-03-01")
    curve = irf.caar_curve(r, [pd.Timestamp("2000-03-01").date()], range(0, 20))
    assert {"h", "caar", "se", "lo", "hi"}.issubset(curve.columns)
    # cumulative abnormal return troughs near the -0.10 impulse
    assert curve["caar"].min() < -0.05


def test_recovery_horizon_detects_return_to_zero():
    r = _series_with_shock("2000-03-01", drop=-0.10, recover_days=8)
    curve = irf.caar_curve(r, [pd.Timestamp("2000-03-01").date()], range(0, 30))
    h = irf.recovery_horizon(curve)
    assert h is not None and h > 0


def test_half_life_positive_for_decaying_curve():
    curve = pd.DataFrame({
        "h": range(0, 10),
        "caar": [-0.1 * (0.7 ** k) for k in range(10)],
        "se": [0.0] * 10, "lo": [0.0] * 10, "hi": [0.0] * 10,
    })
    hl = irf.half_life(curve)
    assert hl is not None and hl > 0
