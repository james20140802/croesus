import datetime
import pandas as pd
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _make_intraday_df(event_date: datetime.date) -> pd.DataFrame:
    """Fake intraday df mimicking fetch_intraday_fomc output."""
    return pd.DataFrame([{
        "event_date": event_date,
        "open_2pm": 560.0,
        "close_4pm": 567.0,
        "return_2pm_4pm": 567.0 / 560.0 - 1,
    }])


def test_intraday_df_structure():
    df = _make_intraday_df(datetime.date(2024, 9, 18))
    assert set(df.columns) == {"event_date", "open_2pm", "close_4pm", "return_2pm_4pm"}
    assert len(df) == 1


def test_return_calculation():
    df = _make_intraday_df(datetime.date(2024, 9, 18))
    expected = 567.0 / 560.0 - 1
    assert abs(df.iloc[0]["return_2pm_4pm"] - expected) < 1e-9


import math
from analysis.intraday_study import compute_intraday_impact


def _make_returns_df(returns: list) -> pd.DataFrame:
    base = datetime.date(2024, 9, 1)
    rows = []
    for i, r in enumerate(returns):
        rows.append({
            "event_date": base + datetime.timedelta(days=i * 30),
            "open_2pm": 500.0,
            "close_4pm": 500.0 * (1 + r),
            "return_2pm_4pm": r,
        })
    return pd.DataFrame(rows)


def test_compute_intraday_impact_basic():
    returns = [0.01, -0.005, 0.02, 0.015, -0.01]
    df = _make_returns_df(returns)
    result = compute_intraday_impact(df["event_date"].tolist(), df)
    assert "per_event" in result
    assert "summary" in result
    assert len(result["per_event"]) == 5
    summary = result["summary"].iloc[0]
    assert summary["n"] == 5
    assert abs(summary["mean"] - sum(returns) / 5) < 1e-9


def test_compute_intraday_impact_t_stat():
    returns = [0.01] * 10
    df = _make_returns_df(returns)
    result = compute_intraday_impact(df["event_date"].tolist(), df)
    summary = result["summary"].iloc[0]
    assert math.isnan(summary["t_stat"]) or summary["t_stat"] > 0


def test_compute_intraday_impact_empty():
    df = pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])
    result = compute_intraday_impact([], df)
    assert result["per_event"].empty
    summary = result["summary"].iloc[0]
    assert summary["n"] == 0


def test_compute_intraday_impact_filters_by_dates():
    returns = [0.01, -0.005, 0.02]
    df = _make_returns_df(returns)
    result = compute_intraday_impact([df["event_date"].iloc[0]], df)
    assert len(result["per_event"]) == 1
