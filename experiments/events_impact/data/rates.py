"""Free rate data from FRED via direct CSV download (no API key required).

Used to compute monetary policy surprise proxy:
  Δ2yr Treasury yield on FOMC day ≈ market's rate expectation revision.
"""
import sys
import datetime
from io import StringIO

import pandas as pd
import requests

_FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
_TIMEOUT = 15


def fetch_fred_series(
    series_id: str,
    start: datetime.date,
    end: datetime.date,
) -> pd.DataFrame:
    """Download a FRED series as a DataFrame indexed by date.

    Returns DataFrame with column 'value'. Missing observations (FRED uses '.')
    are converted to NaN.
    """
    url = _FRED_CSV_URL.format(series_id=series_id)
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch FRED {series_id}: {e}") from e

    df = pd.read_csv(StringIO(resp.text), na_values=".")
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df["value"] = pd.to_numeric(df["value"], errors="coerce")

    mask = (df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end))
    result = df.loc[mask]
    print(
        f"[rates] fetched FRED/{series_id}: {len(result)} rows "
        f"({result.index.min().date()} → {result.index.max().date()})",
        file=sys.stderr,
    )
    return result


def fetch_2yr_yield(start: datetime.date, end: datetime.date) -> pd.DataFrame:
    """2-year Treasury constant maturity yield (DGS2), in percent (e.g. 3.45 = 3.45%).

    Data available from 1976; business days only (weekends/holidays are NaN or absent).
    """
    return fetch_fred_series("DGS2", start, end)
