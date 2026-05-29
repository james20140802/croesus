import datetime
from pathlib import Path

import pandas as pd
import pytest

CSV_PATH = Path(__file__).parent.parent / "events" / "fomc_dates.csv"


@pytest.fixture(scope="module")
def fomc_df():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def test_csv_has_regime_column(fomc_df):
    assert "regime" in fomc_df.columns, "regime column missing"


def test_csv_has_is_emergency_column(fomc_df):
    assert "is_emergency" in fomc_df.columns, "is_emergency column missing"


def test_regime_values_valid(fomc_df):
    valid = {"tightening", "easing", "hold", "crisis"}
    actual = set(fomc_df["regime"].dropna().unique())
    assert actual <= valid, f"Invalid regime values: {actual - valid}"


def test_emergency_dates_are_crisis(fomc_df):
    emergency = fomc_df[fomc_df["is_emergency"] == True]
    dates = set(emergency["date"].tolist())
    assert dates == {datetime.date(2020, 3, 3), datetime.date(2020, 3, 15)}
    assert (emergency["regime"] == "crisis").all()


def test_2015_dec_is_tightening(fomc_df):
    row = fomc_df[fomc_df["date"] == datetime.date(2015, 12, 16)].iloc[0]
    assert row["regime"] == "tightening"


def test_2024_sep_is_easing(fomc_df):
    row = fomc_df[fomc_df["date"] == datetime.date(2024, 9, 18)].iloc[0]
    assert row["regime"] == "easing"


def test_no_null_regime(fomc_df):
    assert fomc_df["regime"].isna().sum() == 0, "Null regime values found"


from events.schema import load_events_csv


def test_load_events_csv_includes_regime(fomc_df):
    df = load_events_csv(CSV_PATH, "fomc")
    assert "regime" in df.columns


def test_load_events_csv_includes_is_emergency(fomc_df):
    df = load_events_csv(CSV_PATH, "fomc")
    assert "is_emergency" in df.columns


def test_load_events_csv_regime_not_null(fomc_df):
    df = load_events_csv(CSV_PATH, "fomc")
    assert df["regime"].isna().sum() == 0
