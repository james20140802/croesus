import numpy as np

from experiments.market_signals.vol_targeting.evaluate import (
    loss_diff_tstat,
    mse_loss,
    qlike_loss,
)


def test_mse_zero_at_perfect_forecast():
    rv = np.array([0.1, 0.2, 0.3])
    assert np.allclose(mse_loss(rv, rv), 0.0)
    assert np.allclose(mse_loss(rv + 0.1, rv), 0.01)


def test_qlike_zero_at_perfect_and_positive_otherwise():
    rv = np.array([0.1, 0.2])
    assert np.allclose(qlike_loss(rv, rv), 0.0)
    assert (qlike_loss(rv * 1.5, rv) > 0).all()
    assert (qlike_loss(rv * 0.5, rv) > 0).all()


def test_loss_diff_tstat_sign():
    rng = np.random.default_rng(0)
    rv = np.abs(rng.normal(0.15, 0.03, 200))
    good = mse_loss(rv + rng.normal(0, 0.01, 200), rv)
    bad = mse_loss(rv + rng.normal(0, 0.05, 200), rv)
    res = loss_diff_tstat(good, bad)
    assert res["mean_diff"] < 0 and res["t"] < -2 and res["n"] == 200
