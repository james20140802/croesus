import numpy as np
import pandas as pd

from experiments.market_signals.timesfm_eval import metrics


def test_directional_hit_rate():
    y = np.array([0.01, -0.02, 0.03, -0.01])
    p = np.array([0.02, -0.01, -0.05, -0.02])  # 3 of 4 signs match
    assert metrics.directional_hit_rate(y, p) == 0.75


def test_skill_score_beats_baseline():
    assert metrics.skill_score(0.5, 1.0) == 0.5      # half the error
    assert metrics.skill_score(2.0, 1.0) == -1.0     # worse than baseline


def test_rolling_origin_eval_with_persistence_forecaster():
    idx = pd.date_range("2000-01-01", periods=120, freq="B")
    series = pd.Series(100 + np.cumsum(np.ones(120)), index=idx)  # +1/day

    def persistence(context, horizon):
        # predict flat = last value repeated => predicts zero return
        return np.repeat(context[-1], horizon)

    df = metrics.rolling_origin_eval(series, persistence,
                                     context_len=30, horizons=[1, 5], step=10)
    assert set(df["h"].unique()) == {1, 5}
    # persistence predicts 0 return; true return is positive here
    assert (df["pred_return"].abs() < 1e-9).all()
    assert (df["true_return"] > 0).all()
