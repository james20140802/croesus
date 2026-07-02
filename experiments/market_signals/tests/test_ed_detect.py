"""로드맵 ③ — 이벤트 소급 탐지 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.detect import dedupe_events, scan_asset_events


def _prices(returns, volumes):
    idx = pd.bdate_range("2020-01-01", periods=len(returns) + 1)
    close = 100 * np.cumprod([1.0] + [1 + r for r in returns])
    return pd.DataFrame({"close": close, "volume": [1e6] + list(volumes)}, index=idx)


def test_scan_detects_3sigma_return_event():
    rng = np.random.RandomState(0)
    rets = list(rng.normal(0, 0.01, 100))
    rets[80] = 0.10  # ≈10σ up-move
    px = _prices(rets, [1e6] * 100)
    ev = scan_asset_events(px)
    hits = ev[(ev["event_type"] == "abnormal_return") & (ev["pos"] == 81)]
    assert len(hits) == 1
    assert hits.iloc[0]["direction"] == "up"
    assert hits.iloc[0]["magnitude"] > 3.0
    assert hits.iloc[0]["date"] == px.index[81]


def test_scan_detects_volume_spike_up_only():
    rng = np.random.RandomState(1)
    rets = list(rng.normal(0, 0.01, 60))
    vols = list(rng.normal(1e6, 5e4, 60))
    vols[50] = 3e6   # 큰 양의 z
    vols[55] = 1e3   # 낮은 거래량은 이벤트 아님
    ev = scan_asset_events(_prices(rets, vols))
    vol_ev = ev[ev["event_type"] == "abnormal_volume"]
    assert 51 in set(vol_ev["pos"])
    assert 56 not in set(vol_ev["pos"])
    assert (vol_ev["direction"] == "up").all()


def test_scan_insufficient_history_no_events():
    rets = [0.2] * 10  # 워밍업(63일) 미만
    ev = scan_asset_events(_prices(rets, [1e6] * 10))
    assert len(ev[ev["event_type"] == "abnormal_return"]) == 0


def test_dedupe_keeps_first_within_gap():
    ev = pd.DataFrame({
        "asset_id": ["A", "A", "A", "B"],
        "date": pd.to_datetime(["2020-01-01", "2020-01-10", "2020-03-01", "2020-01-10"]),
        "pos": [100, 110, 150, 110],
        "event_type": ["abnormal_return"] * 4,
        "direction": ["up"] * 4,
        "magnitude": [3.5, 4.0, 3.2, 5.0],
    })
    out = dedupe_events(ev, min_gap=21)
    # A: 100 채택, 110 제거(gap 10), 150 채택(gap 50); B: 채택
    assert list(out["pos"]) == [100, 150, 110]
