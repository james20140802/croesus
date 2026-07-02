"""Build the point-in-time factor/forward-return panel."""
from __future__ import annotations

import pandas as pd

from experiments.market_signals.cross_sectional.factors import compute_factors_asof
from experiments.market_signals.cross_sectional.returns import forward_returns


def month_end_grid(all_dates, start_year: int = 2010) -> list:
    """Last available trading day of each calendar month from ``start_year`` on."""
    idx = pd.DatetimeIndex(sorted(set(pd.to_datetime(list(all_dates)))))
    idx = idx[idx.year >= start_year]
    if len(idx) == 0:
        return []
    s = pd.Series(idx, index=idx)
    return list(s.groupby([idx.year, idx.month]).last().to_numpy())


def equal_weight_market_return(prices: dict) -> pd.Series:
    """Equal-weight daily return across the universe (market proxy for beta)."""
    rets = [g["close"].pct_change() for g in prices.values()]
    m = pd.concat(rets, axis=1).mean(axis=1)
    m.index = pd.to_datetime(m.index)
    return m.dropna()


def build_panel(prices: dict, rebalance_dates, horizons, market_ret=None) -> pd.DataFrame:
    """Long panel: date, asset_id, factor_name, value, fwd_<h> columns."""
    reb = [pd.Timestamp(d) for d in rebalance_dates]
    recs = []
    for aid, g in prices.items():
        g = g.sort_index()
        gi = g.index
        for as_of in reb:
            if as_of not in gi:
                continue
            factors = compute_factors_asof(g, as_of, market_ret)
            if not factors:
                continue
            fr = forward_returns(g["close"], as_of, horizons)
            for name, val in factors.items():
                rec = {"date": as_of, "asset_id": aid, "factor_name": name, "value": val}
                for h in horizons:
                    rec[f"fwd_{h}"] = fr.get(h, float("nan"))
                recs.append(rec)
    cols = ["date", "asset_id", "factor_name", "value"] + [f"fwd_{h}" for h in horizons]
    return pd.DataFrame.from_records(recs, columns=cols)
