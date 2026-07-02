"""regime_conditional.regimes 테스트."""
import pandas as pd

from croesus.macro.engine import _classify_regime
from experiments.market_signals.regime_conditional.regimes import (
    classify_regime,
    monthly_regimes,
    run_length_summary,
    transition_matrix,
    with_yoy_inflation,
)


def test_classify_matches_production():
    for g in ["Expanding", "Contracting"]:
        for i in ["Rising", "Falling"]:
            assert classify_regime(g, i) == _classify_regime(g, i)


def _mk(dates, vals):
    return pd.Series(vals, index=pd.to_datetime(dates))


def test_monthly_regimes_is_point_in_time():
    # UNRATE 하락(→Expanding), CPILFESL 하락(→Falling) → Goldilocks.
    # 컷오프 이후의 급반전 관측이 새면 라벨이 뒤집힌다 → 안 뒤집혀야 PIT.
    unrate = _mk(["2020-01-01", "2020-02-01", "2020-03-01", "2020-06-01"], [5.0, 4.0, 3.0, 99.0])
    cpi = _mk(["2020-01-01", "2020-02-01", "2020-03-01", "2020-06-01"], [3.0, 2.0, 1.0, 99.0])
    raw = {"UNRATE": unrate, "CPILFESL": cpi}
    out = monthly_regimes(raw, [pd.Timestamp("2020-04-30")])
    assert out.loc[0, "regime"] == "Goldilocks"


def test_with_yoy_inflation_transforms_levels():
    idx = pd.date_range("2020-01-01", periods=25, freq="MS")
    level = pd.Series(range(100, 125), index=idx, dtype=float)
    out = with_yoy_inflation({"CPILFESL": level, "UNRATE": level})
    assert abs(out["CPILFESL"].iloc[0] - 12.0) < 1e-9  # 100→112 = +12%
    assert len(out["CPILFESL"]) == 13
    assert out["UNRATE"].equals(level)  # 성장 계열은 무변환


def test_run_lengths_and_transitions():
    labels = pd.Series(["A", "A", "B", "B", "B", "A"])
    rl = run_length_summary(labels).set_index("regime")
    assert rl.loc["A", "n_months"] == 3 and rl.loc["A", "n_runs"] == 2
    assert rl.loc["B", "avg_run_len"] == 3.0
    tm = transition_matrix(labels)
    assert tm.loc["A", "B"] == 1 and tm.loc["B", "A"] == 1
