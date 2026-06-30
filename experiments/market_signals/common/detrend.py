"""Shared preprocessing transforms for the market_signals experiments.

The point of `identity` is the preprocessing on/off comparison: experiments
loop over TRANSFORMS so the same analysis runs with and without detrending.
"""
import numpy as np
import pandas as pd


def identity(price: pd.Series) -> pd.Series:
    """No-op detrend baseline. Works in log space for comparability."""
    return pd.Series(np.log(price.values), index=price.index, name="identity")


def log_returns(price: pd.Series) -> pd.Series:
    r = np.diff(np.log(price.values))
    return pd.Series(r, index=price.index[1:], name="log_returns")


def demean_drift(series: pd.Series) -> pd.Series:
    return series - series.mean()


def detrend_logprice(price: pd.Series, kind: str = "linear") -> pd.Series:
    logp = np.log(price.values)
    x = np.arange(len(logp), dtype=float)
    if kind == "linear":
        coef = np.polyfit(x, logp, 1)
        trend = np.polyval(coef, x)
    elif kind == "exp":
        # exponential trend in price == linear trend in log price fit by least
        # squares on log scale, then re-expressed; residual is in log space.
        coef = np.polyfit(x, logp, 1)
        trend = np.polyval(coef, x)
    else:
        raise ValueError(f"unknown kind: {kind}")
    return pd.Series(logp - trend, index=price.index, name=f"detrend_{kind}")


def _demean_logret(price: pd.Series) -> pd.Series:
    return demean_drift(log_returns(price))


TRANSFORMS = {
    "identity": identity,
    "demean_logret": _demean_logret,
    "detrend_linear": lambda p: detrend_logprice(p, kind="linear"),
}
