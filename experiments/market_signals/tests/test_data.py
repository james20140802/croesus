import datetime

import pandas as pd

from experiments.market_signals.common import data


def test_indices_registry():
    assert data.INDICES["US_IDX_SP500"] == "^GSPC"
    assert data.INDICES["US_IDX_NASDAQ"] == "^IXIC"


def test_load_prices_returns_sorted_adjusted_close():
    df = data.load_prices(
        "US_IDX_SP500", "^GSPC",
        datetime.date(2020, 1, 1), datetime.date(2020, 3, 1),
    )
    assert list(df.columns) == ["adjusted_close"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    assert len(df) > 20  # ~40 trading days in two months
