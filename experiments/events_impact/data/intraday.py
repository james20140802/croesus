"""SPY 1h intraday price data for FOMC event days.

Returns 2pm→4pm ET return for each FOMC event date.
Coverage: last 730 days (yfinance limitation for hourly data).
Caches in DuckDB prices_intraday table.
"""
import datetime
import sys
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import yfinance as yf

from config import DB_PATH

ET = ZoneInfo("America/New_York")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_intraday (
    asset_id  TEXT,
    datetime  TIMESTAMP,
    open      DOUBLE,
    high      DOUBLE,
    low       DOUBLE,
    close     DOUBLE,
    volume    BIGINT,
    source    TEXT,
    PRIMARY KEY (asset_id, datetime)
)
"""


def _get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(_SCHEMA)
    return conn


def _fetch_spy_hourly(start: datetime.date, end: datetime.date) -> pd.DataFrame:
    raw = yf.download(
        "SPY",
        start=str(start),
        end=str(end + datetime.timedelta(days=1)),
        interval="1h",
        auto_adjust=False,
        progress=False,
    )
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC").tz_convert(ET)
    else:
        raw.index = raw.index.tz_convert(ET)
    return raw


def fetch_intraday_fomc(
    event_dates: list[datetime.date],
) -> pd.DataFrame:
    """Return 2pm→4pm ET returns for FOMC event dates.

    Columns: event_date, open_2pm, close_4pm, return_2pm_4pm
    Dates outside the yfinance 730-day window are silently omitted.
    """
    if not event_dates:
        return pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])

    cutoff = datetime.date.today() - datetime.timedelta(days=729)
    eligible = sorted(d for d in event_dates if d >= cutoff)
    if not eligible:
        print("[intraday] no events within yfinance 730-day window", file=sys.stderr)
        return pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])

    conn = _get_connection()

    cached = conn.execute(
        """SELECT DISTINCT CAST(datetime AS DATE) AS dt
           FROM prices_intraday
           WHERE asset_id = 'SPY_1H'
             AND CAST(datetime AS DATE) BETWEEN ? AND ?""",
        [min(eligible), max(eligible)],
    ).fetchdf()
    cached_dates = set(cached["dt"].astype(str)) if not cached.empty else set()

    need_fetch = [d for d in eligible if str(d) not in cached_dates]
    if need_fetch:
        print(
            f"[intraday] fetching SPY 1h {min(need_fetch)} → {max(need_fetch)}",
            file=sys.stderr,
        )
        raw = _fetch_spy_hourly(min(need_fetch), max(need_fetch))
        if not raw.empty:
            adj_col = "Adj Close" if "Adj Close" in raw.columns else "Close"
            rows = [
                (
                    "SPY_1H",
                    ts.isoformat(),
                    float(row.get("Open", float("nan"))),
                    float(row.get("High", float("nan"))),
                    float(row.get("Low", float("nan"))),
                    float(row.get(adj_col, float("nan"))),
                    int(row.get("Volume", 0) or 0),
                    "yfinance",
                )
                for ts, row in raw.iterrows()
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO prices_intraday VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            print(f"[intraday] cached {len(rows)} hourly bars", file=sys.stderr)

    results = []
    for ed in eligible:
        day_data = conn.execute(
            """SELECT datetime, open, close
               FROM prices_intraday
               WHERE asset_id = 'SPY_1H'
                 AND CAST(datetime AS DATE) = ?
               ORDER BY datetime""",
            [ed],
        ).fetchdf()

        if day_data.empty:
            print(f"[intraday] no data for {ed}, skip", file=sys.stderr)
            continue

        # Timestamps stored in DuckDB are already ET (yfinance converts before insert).
        # Localise as ET directly — do NOT treat as UTC first.
        day_data["dt_et"] = pd.to_datetime(day_data["datetime"]).dt.tz_localize(ET)
        day_data["hour"] = day_data["dt_et"].dt.hour

        bar_14 = day_data[day_data["hour"] == 14]
        bar_15 = day_data[day_data["hour"] == 15]

        if bar_14.empty or bar_15.empty:
            print(f"[intraday] missing 2pm or 3pm bar for {ed}, skip", file=sys.stderr)
            continue

        open_2pm = float(bar_14.iloc[0]["open"])
        close_4pm = float(bar_15.iloc[-1]["close"])
        ret = close_4pm / open_2pm - 1

        results.append({
            "event_date": ed,
            "open_2pm": open_2pm,
            "close_4pm": close_4pm,
            "return_2pm_4pm": ret,
        })

    conn.close()
    return (
        pd.DataFrame(results)
        if results
        else pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])
    )
