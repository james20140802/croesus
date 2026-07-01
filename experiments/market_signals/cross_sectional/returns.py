"""Forward returns for the IC panel.

At ``as_of`` (a date present in the series), the h-day forward return is
``close[pos+h]/close[pos] - 1`` using the asset's own trading days. Horizons that
run past the end of the series are omitted (no future data available yet).
"""
from __future__ import annotations

import pandas as pd


def forward_returns(close: pd.Series, as_of, horizons) -> dict:
    close = close.sort_index()
    pos = int(close.index.get_indexer([pd.Timestamp(as_of)])[0])
    if pos < 0:
        return {}
    n = len(close)
    out: dict[int, float] = {}
    for h in horizons:
        if pos + h < n:
            out[h] = float(close.iloc[pos + h] / close.iloc[pos] - 1)
    return out
