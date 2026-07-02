"""날짜 군집(Fama-MacBeth식) CAAR 추론 + 무작위 날짜 placebo."""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.market_signals.cross_sectional.stats import newey_west_se


def caar_table(car: pd.DataFrame, event_dates: pd.Series) -> pd.DataFrame:
    """CAAR(h) with same-day events collapsed to one date-level observation.

    Overlapping post-event windows across nearby dates leave serial correlation
    in the date series — Newey-West with lags=h covers exactly that overlap.
    """
    rows = []
    for h in car.columns:
        s = car[h].dropna()
        if len(s) == 0:
            rows.append({"h": int(h), "caar": np.nan, "t": np.nan,
                         "n_dates": 0, "n_events": 0})
            continue
        by_date = s.groupby(event_dates.loc[s.index]).mean().sort_index()
        se = newey_west_se(by_date.to_numpy(), lags=int(h))
        mean = float(by_date.mean())
        t = mean / se if np.isfinite(se) and se > 0 else np.nan
        rows.append({"h": int(h), "caar": mean, "t": float(t),
                     "n_dates": int(len(by_date)), "n_events": int(len(s))})
    return pd.DataFrame(rows)


def placebo_events(events: pd.DataFrame, prices: dict[str, pd.DataFrame],
                   seed: int = 42, warmup: int = 64) -> pd.DataFrame:
    """Same per-asset event counts, uniformly random positions — the null."""
    rng = np.random.RandomState(seed)
    out = events.copy().reset_index(drop=True)
    for aid, grp in out.groupby("asset_id"):
        n = len(prices[aid])
        pos = rng.randint(warmup, n - 1, size=len(grp))  # [warmup, n-2]
        out.loc[grp.index, "pos"] = pos
        out.loc[grp.index, "date"] = prices[aid].index[pos]
    return out
