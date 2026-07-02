"""로드맵 ③ — calendar-time 이벤트 포트폴리오 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.portfolio import event_portfolio_returns


def _ex(vals, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(vals))
    return pd.Series(vals, index=idx)


def test_single_up_event_holds_next_days():
    ex = {"A": _ex([np.nan, 0.01, 0.02, 0.03, 0.04])}
    ev = pd.DataFrame({"asset_id": ["A"], "date": [ex["A"].index[1]], "pos": [1],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    ret, to = event_portfolio_returns(ex, ev, hold=2, cost_bps=0.0)
    # 보유일: rows 2,3 (T+1..T+2), weight=1 → 수익 0.02, 0.03; 그 외 0
    assert abs(ret.loc[ex["A"].index[2]] - 0.02) < 1e-12
    assert abs(ret.loc[ex["A"].index[3]] - 0.03) < 1e-12
    assert abs(ret.loc[ex["A"].index[4]]) < 1e-12


def test_down_event_is_short():
    ex = {"A": _ex([np.nan, 0.01, 0.02])}
    ev = pd.DataFrame({"asset_id": ["A"], "date": [ex["A"].index[1]], "pos": [1],
                       "event_type": ["abnormal_return"], "direction": ["down"],
                       "magnitude": [-4.0]})
    ret, _ = event_portfolio_returns(ex, ev, hold=1)
    assert abs(ret.loc[ex["A"].index[2]] + 0.02) < 1e-12


def test_cost_reduces_return_on_rebalance_days():
    ex = {"A": _ex([np.nan, 0.01, 0.0, 0.0])}
    ev = pd.DataFrame({"asset_id": ["A"], "date": [ex["A"].index[1]], "pos": [1],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    r0, _ = event_portfolio_returns(ex, ev, hold=1, cost_bps=0.0)
    r10, to = event_portfolio_returns(ex, ev, hold=1, cost_bps=10.0)
    # 진입일(row 2)과 청산일(row 3)에 |Δw|=1 → 각 10bps 비용
    assert abs((r0 - r10).loc[ex["A"].index[2]] - 0.001) < 1e-12
    assert abs((r0 - r10).loc[ex["A"].index[3]] - 0.001) < 1e-12
    assert to > 0
