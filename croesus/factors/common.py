from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

COMMON_FACTOR_NAMES = (
    "momentum_1m",
    "momentum_3m",
    "momentum_6m",
    "volatility_3m",
    "liquidity_1m",
    "above_200d_ma",
)


@dataclass(frozen=True)
class FactorValue:
    asset_id: str
    date: date
    factor_name: str
    value: float


def compute_common_factors(asset_id: str, prices: pd.DataFrame) -> list[FactorValue]:
    if len(prices) < 200:
        return []

    data = prices.sort_values("date").copy()
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
    data = data.dropna(subset=["date", "close", "volume"])
    if len(data) < 200:
        return []

    latest = data.iloc[-1]
    latest_date = pd.Timestamp(latest["date"]).date()
    close = data["close"]
    returns = close.pct_change()

    values = {
        "momentum_1m": _momentum(close, 21),
        "momentum_3m": _momentum(close, 63),
        "momentum_6m": _momentum(close, 126),
        "volatility_3m": float(returns.tail(63).std()),
        "liquidity_1m": float((data["close"] * data["volume"]).tail(21).mean()),
        "above_200d_ma": 1.0 if float(latest["close"]) > float(close.tail(200).mean()) else 0.0,
    }
    return [
        FactorValue(asset_id=asset_id, date=latest_date, factor_name=name, value=value)
        for name, value in values.items()
        if pd.notna(value)
    ]


def _momentum(close: pd.Series, periods: int) -> float:
    if len(close) <= periods:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-1 - periods] - 1)
