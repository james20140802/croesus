import datetime
from pathlib import Path
import pandas as pd

CSV_PATH = Path(__file__).parent.parent / "events" / "fomc_dates.csv"


def test_csv_has_regime_column():
    df = pd.read_csv(CSV_PATH)
    assert "regime" in df.columns, "regime 컬럼 없음"


def test_csv_has_is_emergency_column():
    df = pd.read_csv(CSV_PATH)
    assert "is_emergency" in df.columns, "is_emergency 컬럼 없음"


def test_regime_values_valid():
    df = pd.read_csv(CSV_PATH)
    valid = {"tightening", "easing", "hold", "crisis"}
    actual = set(df["regime"].dropna().unique())
    assert actual <= valid, f"유효하지 않은 regime 값: {actual - valid}"


def test_emergency_dates_are_crisis():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    emergency = df[df["is_emergency"] == True]
    assert len(emergency) == 2
    dates = set(emergency["date"].tolist())
    assert dates == {datetime.date(2020, 3, 3), datetime.date(2020, 3, 15)}
    assert (emergency["regime"] == "crisis").all()


def test_2015_dec_is_tightening():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    row = df[df["date"] == datetime.date(2015, 12, 16)].iloc[0]
    assert row["regime"] == "tightening"


def test_2024_sep_is_easing():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    row = df[df["date"] == datetime.date(2024, 9, 18)].iloc[0]
    assert row["regime"] == "easing"


def test_no_null_regime():
    df = pd.read_csv(CSV_PATH)
    assert df["regime"].isna().sum() == 0, "regime에 null 값 있음"
