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
