"""regime_conditional.conditional 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.regime_conditional.conditional import (
    between_stat,
    join_regime,
    post_change_table,
    regime_table,
    shift_placebo,
)


def _regimes(dates, labels):
    return pd.DataFrame({"date": pd.to_datetime(dates), "regime": labels})


def test_join_regime_uses_latest_known_label():
    perdate = pd.DataFrame({"date": pd.to_datetime(["2020-03-31", "2020-04-15"]),
                            "ls": [0.01, 0.02]})
    regimes = _regimes(["2020-02-29", "2020-03-31"], ["A", "B"])
    j = join_regime(perdate, regimes)
    assert list(j["regime"]) == ["B", "B"]


def test_regime_table_stats():
    j = pd.DataFrame({"regime": ["A", "A", "B", "B"], "ls": [0.01, 0.03, -0.01, -0.03]})
    t = regime_table(j).set_index("regime")
    assert t.loc["A", "n"] == 2
    assert abs(t.loc["A", "mean"] - 0.02) < 1e-12
    assert t.loc["A", "sharpe"] > 0 > t.loc["B", "sharpe"]


def test_between_stat_weighted_variance():
    r = np.array([1.0, 1.0, -1.0, -1.0])
    lab = np.array(["A", "A", "B", "B"])
    assert abs(between_stat(r, lab) - 1.0) < 1e-12


def test_shift_placebo_separated_vs_constant():
    lab = np.array(["A"] * 30 + ["B"] * 30)
    strong = np.r_[np.ones(30), -np.ones(30)] + np.linspace(0, 0.01, 60)
    obs, p_strong = shift_placebo(strong, lab)
    assert obs > 0 and p_strong < 0.1
    _, p_flat = shift_placebo(np.zeros(60), lab)
    assert p_flat == 1.0


def test_post_change_table_splits_first_month_after_switch():
    j = pd.DataFrame({"date": pd.date_range("2020-01-31", periods=5, freq="M"),
                      "regime": ["A", "A", "B", "B", "A"],
                      "ls": [0.01, 0.02, 0.10, 0.03, 0.20]})
    t = post_change_table(j).set_index("phase")
    assert t.loc["post_change", "n"] == 2      # B 첫 달(0.10), A 복귀 첫 달(0.20)
    assert abs(t.loc["post_change", "mean"] - 0.15) < 1e-12
    assert t.loc["steady", "n"] == 3
