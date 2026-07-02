"""Calendar-time 이벤트 포트폴리오 — 이벤트 방향으로 [T+1, T+hold] 보유."""
from __future__ import annotations

import numpy as np
import pandas as pd


def event_portfolio_returns(excess_by_asset: dict[str, pd.Series], events: pd.DataFrame,
                            hold: int = 21, cost_bps: float = 0.0) -> tuple[pd.Series, float]:
    """Daily net returns of a signed, gross-1-normalized event book + avg daily turnover."""
    sig: dict[str, pd.Series] = {}
    for aid, grp in events.groupby("asset_id"):
        ex = excess_by_asset[aid]
        s = np.zeros(len(ex))
        for pos, d in zip(grp["pos"], grp["direction"]):
            sgn = 1.0 if d == "up" else -1.0
            s[int(pos) + 1: int(pos) + 1 + hold] += sgn
        sig[aid] = pd.Series(s, index=ex.index)
    signal = pd.DataFrame(sig).fillna(0.0).sort_index()
    gross = signal.abs().sum(axis=1)
    weights = signal.div(gross.where(gross > 0), axis=0).fillna(0.0)
    excess = pd.DataFrame({aid: excess_by_asset[aid] for aid in signal.columns})
    excess = excess.reindex(weights.index)
    ret = (weights * excess).sum(axis=1)  # NaN excess → 그 종목 기여 0
    turnover = weights.diff().abs().sum(axis=1)
    if len(turnover) > 0:
        turnover.iloc[0] = float(weights.iloc[0].abs().sum())
    net = (ret - turnover * cost_bps / 1e4).rename("event_port")
    return net, float(turnover.mean())
