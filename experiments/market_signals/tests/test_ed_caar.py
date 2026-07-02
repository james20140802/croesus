"""로드맵 ③ — CAAR 추론 + placebo 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.caar import caar_table, placebo_events


def test_caar_clusters_same_day_events():
    # h=1 CAR: 날짜 d1에 2건(0.10, 0.30), d2에 1건(0.20) → 날짜 평균 [0.20, 0.20]
    car = pd.DataFrame({1: [0.10, 0.30, 0.20]}, index=[0, 1, 2])
    dates = pd.Series(pd.to_datetime(["2020-01-05", "2020-01-05", "2020-02-01"]),
                      index=[0, 1, 2])
    tbl = caar_table(car, dates)
    row = tbl[tbl["h"] == 1].iloc[0]
    assert abs(row["caar"] - 0.20) < 1e-12
    assert row["n_dates"] == 2 and row["n_events"] == 3


def test_caar_handles_all_nan_horizon():
    car = pd.DataFrame({1: [0.1], 2: [np.nan]}, index=[0])
    dates = pd.Series(pd.to_datetime(["2020-01-05"]), index=[0])
    tbl = caar_table(car, dates)
    assert tbl[tbl["h"] == 2].iloc[0]["n_events"] == 0


def test_placebo_preserves_counts_and_is_reproducible():
    idx = pd.bdate_range("2019-01-01", periods=300)
    prices = {"A": pd.DataFrame({"close": np.linspace(50, 60, 300),
                                 "volume": 1e6}, index=idx)}
    ev = pd.DataFrame({"asset_id": ["A"] * 3,
                       "date": [idx[100], idx[150], idx[200]],
                       "pos": [100, 150, 200],
                       "event_type": ["abnormal_return"] * 3,
                       "direction": ["up", "down", "up"],
                       "magnitude": [3.5, -4.0, 3.1]})
    p1 = placebo_events(ev, prices, seed=7)
    p2 = placebo_events(ev, prices, seed=7)
    assert len(p1) == 3
    assert (p1["pos"] >= 64).all() and (p1["pos"] <= 298).all()
    assert list(p1["pos"]) == list(p2["pos"])  # 재현성
    assert (p1["date"].to_numpy() == idx[p1["pos"]].to_numpy()).all()
