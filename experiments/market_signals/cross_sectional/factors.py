"""As-of price factors — a faithful replica of ``croesus/factors/common.py``.

Given a price history and an ``as_of`` date, compute the common factors using
only data up to ``as_of`` (point-in-time; no look-ahead). Returns an empty dict
when there is insufficient history, matching the production skip behaviour.
"""
from __future__ import annotations

import pandas as pd

FACTOR_NAMES = (
    "momentum_1m",
    "momentum_3m",
    "momentum_6m",
    "volatility_3m",
    "liquidity_1m",
    "above_200d_ma",
    "beta_1y",
)

_BETA_WINDOW = 252


def _momentum(close: pd.Series, k: int) -> float:
    return float(close.iloc[-1] / close.iloc[-1 - k] - 1) if len(close) > k else float("nan")


def compute_factors_asof(hist: pd.DataFrame, as_of, market_ret: "pd.Series | None" = None) -> dict:
    """Compute factors from ``hist`` sliced to ``as_of`` (inclusive).

    ``hist`` has a DatetimeIndex and ``close``/``volume`` columns. ``market_ret``
    (index=date, daily returns) enables ``beta_1y`` when supplied.
    """
    data = hist.loc[:as_of].dropna(subset=["close", "volume"])
    if len(data) < 200:
        return {}
    close, volume = data["close"], data["volume"]
    returns = close.pct_change()

    out = {
        "momentum_1m": _momentum(close, 21),
        "momentum_3m": _momentum(close, 63),
        "momentum_6m": _momentum(close, 126),
        "volatility_3m": float(returns.tail(63).std()),
        "liquidity_1m": float((close * volume).tail(21).mean()),
        "above_200d_ma": 1.0 if float(close.iloc[-1]) > float(close.tail(200).mean()) else 0.0,
    }

    if market_ret is not None:
        asset_ret = returns.dropna()
        common = asset_ret.index.intersection(market_ret.index)[-_BETA_WINDOW:]
        if len(common) >= 60:
            x = market_ret.loc[common].to_numpy()
            y = asset_ret.loc[common].to_numpy()
            var = float(((x - x.mean()) ** 2).sum())
            if var > 0:
                out["beta_1y"] = float(((x - x.mean()) * (y - y.mean())).sum() / var)

    return {k: v for k, v in out.items() if pd.notna(v)}
