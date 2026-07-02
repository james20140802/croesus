"""regime_conditional.fred 테스트."""
import pandas as pd

from experiments.market_signals.regime_conditional.fred import as_of_view, parse_fredgraph


def test_parse_fredgraph_drops_dot_missing():
    text = "observation_date,XX\n2020-01-01,1.0\n2020-02-01,.\n2020-03-01,3.0\n"
    s = parse_fredgraph(text)
    assert list(s.values) == [1.0, 3.0]
    assert s.index[1] == pd.Timestamp("2020-03-01")


def test_as_of_view_applies_publication_lag():
    idx = pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01"])
    raw = {"UNRATE": pd.Series([1.0, 2.0, 3.0], index=idx)}
    # cutoff = 3/15 - 40d = 2/4 → 1월·2월 관측만 보임
    view = as_of_view(raw, pd.Timestamp("2020-03-15"), lags={"UNRATE": 40})
    assert len(view["UNRATE"]) == 2
    assert view["UNRATE"].index[-1] == pd.Timestamp("2020-02-01")


def test_as_of_view_drops_empty_series():
    idx = pd.to_datetime(["2020-03-01"])
    raw = {"UNRATE": pd.Series([1.0], index=idx)}
    view = as_of_view(raw, pd.Timestamp("2020-01-15"), lags={"UNRATE": 40})
    assert "UNRATE" not in view
