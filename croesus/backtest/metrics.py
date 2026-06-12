"""Pure metric functions over equity curves.

All functions accept a ``pd.Series`` with a date index and return scalar
values.  Short or flat curves return ``None`` rather than raising — callers
must handle ``None`` before formatting.
"""
from __future__ import annotations

import math

import pandas as pd


def cagr(curve: pd.Series) -> float | None:
    """Compound Annual Growth Rate over the full equity curve.

    Returns ``None`` if the curve has fewer than 2 points or the initial
    value is zero/negative.
    """
    if curve is None or len(curve) < 2:
        return None
    start_val = float(curve.iloc[0])
    end_val = float(curve.iloc[-1])
    if start_val <= 0 or end_val <= 0:
        return None
    start_date = _to_date(curve.index[0])
    end_date = _to_date(curve.index[-1])
    days = (end_date - start_date).days
    if days <= 0:
        return None
    years = days / 365.25
    return float((end_val / start_val) ** (1.0 / years) - 1.0)


def sharpe(curve: pd.Series, rf: float = 0.0) -> float | None:
    """Annualised Sharpe ratio (daily returns, √252 scaling, zero risk-free rate by default).

    Returns ``None`` if the curve has fewer than 2 points or daily return
    standard deviation is zero.
    """
    if curve is None or len(curve) < 2:
        return None
    daily_returns = curve.pct_change().dropna()
    if len(daily_returns) < 1:
        return None
    std = float(daily_returns.std())
    if std == 0.0 or math.isnan(std):
        return None
    mean = float(daily_returns.mean()) - rf / 252.0
    return float(mean / std * math.sqrt(252))


def max_drawdown(curve: pd.Series) -> float | None:
    """Maximum peak-to-trough drawdown as a negative fraction (e.g. -0.25 = -25%).

    Returns ``None`` if the curve has fewer than 2 points.
    """
    if curve is None or len(curve) < 2:
        return None
    rolling_max = curve.cummax()
    drawdown = (curve - rolling_max) / rolling_max
    mdd = float(drawdown.min())
    return mdd if not math.isnan(mdd) else None


def total_return(curve: pd.Series) -> float | None:
    """Simple total return from first to last value (e.g. 0.35 = +35%).

    Returns ``None`` if the curve has fewer than 2 points or start value is zero.
    """
    if curve is None or len(curve) < 2:
        return None
    start_val = float(curve.iloc[0])
    end_val = float(curve.iloc[-1])
    if start_val == 0.0:
        return None
    return float(end_val / start_val - 1.0)


def summarize(curve: pd.Series) -> dict[str, float | None]:
    """Return all standard metrics as a single dict.

    Keys: ``cagr``, ``sharpe``, ``max_drawdown``, ``total_return``.
    Values are ``None`` where undefined (short curves, flat curves, etc.).
    """
    return {
        "cagr": cagr(curve),
        "sharpe": sharpe(curve),
        "max_drawdown": max_drawdown(curve),
        "total_return": total_return(curve),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_date(value: object):  # type: ignore[return]
    """Convert index entry to a ``datetime.date``."""
    import datetime

    if isinstance(value, datetime.date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    return pd.Timestamp(value).date()
