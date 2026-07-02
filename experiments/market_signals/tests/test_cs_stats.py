import numpy as np

from experiments.market_signals.cross_sectional.stats import newey_west_se, permutation_ic_null


def test_newey_west_se_positive():
    x = np.array([0.1, -0.05, 0.2, 0.0, 0.15, -0.1, 0.05, 0.08])
    se = newey_west_se(x)
    assert se > 0 and np.isfinite(se)


def test_newey_west_zero_lag_matches_ols_se():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    se0 = newey_west_se(x, lags=0)
    assert abs(se0 - x.std(ddof=0) / np.sqrt(len(x))) < 1e-9


def test_permutation_null_centered_near_zero():
    rng = np.random.default_rng(0)
    v = rng.normal(size=200)
    f = rng.normal(size=200)
    null = permutation_ic_null(v, f, n=300, seed=1)
    assert abs(null.mean()) < 0.05 and len(null) == 300
