from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from croesus.factors.equity.valuation import compute_beta

COMMON_FACTOR_NAMES = (
    "momentum_1m",
    "momentum_3m",
    "momentum_6m",
    "volatility_3m",
    "liquidity_1m",
    "above_200d_ma",
    "beta_1y",
)

# Trailing window for beta (≈1 trading year). Aligned with the market series by
# date, so beta stays point-in-time in the backtest (stock dates are ≤ as_of).
_BETA_WINDOW = 252


@dataclass(frozen=True)
class FactorValue:
    asset_id: str
    date: date
    factor_name: str
    value: float


def compute_common_factors(
    asset_id: str,
    prices: pd.DataFrame,
    market_returns: dict[date, float] | None = None,
) -> list[FactorValue]:
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
    # Beta vs the market (systematic risk) — only when a market return series is
    # supplied. Distinct from volatility_3m (total risk); the BAB literature
    # ranks low-beta as the higher-Sharpe defensive factor.
    if market_returns is not None:
        beta = _beta(data, market_returns)
        if beta is not None:
            values["beta_1y"] = beta
    return [
        FactorValue(asset_id=asset_id, date=latest_date, factor_name=name, value=value)
        for name, value in values.items()
        if pd.notna(value)
    ]


def _momentum(close: pd.Series, periods: int) -> float:
    if len(close) <= periods:
        return float("nan")
    return float(close.iloc[-1] / close.iloc[-1 - periods] - 1)


def _beta(data: pd.DataFrame, market_returns: dict[date, float]) -> float | None:
    """Trailing-window beta of the asset vs the market, aligned by date.

    Intersecting on the asset's own (point-in-time) dates keeps the backtest
    honest: the asset frame is already sliced to ``as_of``, so no future market
    return leaks in. Returns ``None`` when the overlap is too short.
    """
    closes = data[["date", "close"]].copy()
    closes["d"] = [pd.Timestamp(x).date() for x in closes["date"]]
    series = closes.set_index("d")["close"]
    asset_ret = series.pct_change().dropna()
    common = sorted(d for d in asset_ret.index if d in market_returns)[-_BETA_WINDOW:]
    if not common:
        return None
    asset_seq = [float(asset_ret[d]) for d in common]
    market_seq = [market_returns[d] for d in common]
    return compute_beta(asset_seq, market_seq)
