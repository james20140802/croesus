"""croesus/events/detectors.py의 가격 기반 규칙을 과거 전체에 소급 적용.

프로덕션 events 테이블은 5일치뿐(역사화 갭)이라, 동일 규칙(abnormal_return 3σ,
abnormal_volume z≥2 상방)을 30년 이력에 벡터화 재계산한다. 두 규칙 모두 trailing
윈도만 쓰므로 look-ahead가 없다. 파라미터는 프로덕션과 동일하게 고정(튜닝 금지).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RETURN_WINDOW = 63
RETURN_SIGMA_MULT = 3.0
VOLUME_WINDOW = 21
VOLUME_Z_THRESHOLD = 2.0

COLUMNS = ["date", "pos", "event_type", "direction", "magnitude"]


def scan_asset_events(prices: pd.DataFrame) -> pd.DataFrame:
    """One asset's full history -> events with integer row positions.

    ``pos`` indexes rows of ``prices`` so CAR slicing can use integer offsets
    into the asset's own return series.
    """
    df = prices.sort_index()
    close = pd.to_numeric(df["close"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")
    ret = close.pct_change()
    ret = ret.where(np.isfinite(ret))

    sigma = ret.rolling(RETURN_WINDOW).std().shift(1)
    mult = ret / sigma
    r_hit = ((mult.abs() >= RETURN_SIGMA_MULT) & (sigma > 0)).fillna(False)

    vmean = volume.rolling(VOLUME_WINDOW).mean().shift(1)
    vstd = volume.rolling(VOLUME_WINDOW).std().shift(1)
    z = (volume - vmean) / vstd
    v_hit = ((z >= VOLUME_Z_THRESHOLD) & (vstd > 0)).fillna(False)

    rows = []
    for i in np.flatnonzero(r_hit.to_numpy()):
        rows.append({"date": df.index[i], "pos": int(i),
                     "event_type": "abnormal_return",
                     "direction": "up" if ret.iloc[i] > 0 else "down",
                     "magnitude": float(mult.iloc[i])})
    for i in np.flatnonzero(v_hit.to_numpy()):
        rows.append({"date": df.index[i], "pos": int(i),
                     "event_type": "abnormal_volume", "direction": "up",
                     "magnitude": float(z.iloc[i])})
    return pd.DataFrame(rows, columns=COLUMNS)


def dedupe_events(events: pd.DataFrame, min_gap: int = 21) -> pd.DataFrame:
    """Per (asset_id, event_type): drop events within min_gap rows of the last kept one."""
    keep: list[int] = []
    for _, grp in events.sort_values("pos").groupby(["asset_id", "event_type"], sort=False):
        last = -(10 ** 9)
        for idx, pos in zip(grp.index, grp["pos"]):
            if pos - last >= min_gap:
                keep.append(idx)
                last = pos
    return events.loc[sorted(keep)].reset_index(drop=True)
