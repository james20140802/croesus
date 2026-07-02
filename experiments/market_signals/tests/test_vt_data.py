import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.data import equal_weight_returns


def _frame(closes, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"close": closes}, index=idx)


def test_equal_weight_is_mean_of_daily_returns():
    prices = {"a": _frame([100, 110, 121]), "b": _frame([100, 90, 99])}
    ew = equal_weight_returns(prices, min_names=2)
    assert len(ew) == 2
    assert abs(ew.iloc[0] - np.mean([0.10, -0.10])) < 1e-12
    assert abs(ew.iloc[1] - np.mean([0.10, 0.10])) < 1e-12


def test_min_names_filters_thin_days():
    prices = {"a": _frame([100, 110, 121]), "b": _frame([100, 90])}
    ew = equal_weight_returns(prices, min_names=2)
    assert len(ew) == 1  # day3 has only 'a'
