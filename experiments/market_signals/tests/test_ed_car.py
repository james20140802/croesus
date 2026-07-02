"""로드맵 ③ — CAR 경로 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.car import asset_excess, event_car_paths


def _mk():
    idx = pd.bdate_range("2020-01-01", periods=6)
    px = pd.DataFrame({"close": [100, 110, 121, 133.1, 146.41, 161.051],
                       "volume": [1e6] * 6}, index=idx)  # 매일 +10%
    mkt = pd.Series(0.02, index=idx)
    return {"A": px}, mkt


def test_asset_excess_alignment():
    prices, mkt = _mk()
    ex = asset_excess(prices, mkt)["A"]
    assert len(ex) == 6 and np.isnan(ex.iloc[0])  # 첫날 수익률 NaN
    assert abs(ex.iloc[1] - 0.08) < 1e-9  # 0.10 - 0.02


def test_event_car_paths_cumulative_and_padding():
    prices, mkt = _mk()
    ex = asset_excess(prices, mkt)
    ev = pd.DataFrame({"asset_id": ["A"], "date": [prices["A"].index[2]], "pos": [2],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    car = event_car_paths(ex, ev, horizon=5)
    # T+1..T+3 = rows 3,4,5의 excess ≈ 0.08씩 → CAR 0.08, 0.16, 0.24; T+4,5는 NaN
    assert abs(car.loc[0, 1] - 0.08) < 1e-6
    assert abs(car.loc[0, 3] - 0.24) < 1e-6
    assert np.isnan(car.loc[0, 4]) and np.isnan(car.loc[0, 5])


def test_event_on_last_row_all_nan():
    prices, mkt = _mk()
    ex = asset_excess(prices, mkt)
    ev = pd.DataFrame({"asset_id": ["A"], "date": [prices["A"].index[-1]], "pos": [5],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    car = event_car_paths(ex, ev, horizon=3)
    assert car.loc[0].isna().all()
