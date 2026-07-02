"""이벤트별 시장조정 누적초과수익(CAR) 경로."""
from __future__ import annotations

import numpy as np
import pandas as pd


def asset_excess(prices: dict[str, pd.DataFrame], market: pd.Series) -> dict[str, pd.Series]:
    """Per-asset daily excess return, index-aligned to each asset's price frame."""
    out: dict[str, pd.Series] = {}
    for aid, df in prices.items():
        ret = pd.to_numeric(df["close"], errors="coerce").pct_change()
        ret = ret.where(np.isfinite(ret))
        out[aid] = ret - market.reindex(ret.index)
    return out


def event_car_paths(excess_by_asset: dict[str, pd.Series], events: pd.DataFrame,
                    horizon: int = 60) -> pd.DataFrame:
    """CAR(T+1..T+k) per event. Rows follow events.index; short tails are NaN-padded.

    Missing excess days inside a live path contribute 0 (nan_to_num) — delistings
    truncate the path instead of poisoning it.
    """
    out = np.full((len(events), horizon), np.nan)
    row_of = {idx: r for r, idx in enumerate(events.index)}
    for aid, grp in events.groupby("asset_id"):
        ex = excess_by_asset[aid].to_numpy(dtype=float)
        for idx, pos in zip(grp.index, grp["pos"]):
            fwd = ex[int(pos) + 1: int(pos) + 1 + horizon]
            if len(fwd) == 0 or np.all(np.isnan(fwd)):
                continue
            out[row_of[idx], : len(fwd)] = np.cumsum(np.nan_to_num(fwd))
    return pd.DataFrame(out, index=events.index, columns=list(range(1, horizon + 1)))
