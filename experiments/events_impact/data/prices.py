"""S&P 500 (and any ticker) daily price data.

DuckDB read-through cache: checks prices_daily for existing data,
fetches missing ranges from yfinance, then upserts and returns.

Schema aligns with Sprint 001 data-pipeline.md (prices_daily table).
"""
import datetime
import sys
from pathlib import Path

import duckdb
import pandas as pd
import yfinance as yf

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_daily (
    asset_id TEXT,
    date DATE,
    open DOUBLE,
    high DOUBLE,
    low DOUBLE,
    close DOUBLE,
    adjusted_close DOUBLE,
    volume BIGINT,
    source TEXT,
    PRIMARY KEY (asset_id, date)
)
"""


def _get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(_SCHEMA)
    return conn


def _fetch_from_yfinance(
    ticker: str,
    start: datetime.date,
    end: datetime.date,
) -> pd.DataFrame:
    raw = yf.download(
        ticker,
        start=str(start),
        end=str(end + datetime.timedelta(days=1)),
        auto_adjust=False,
        progress=False,
    )
    if raw.empty:
        return pd.DataFrame()
    # yfinance may return MultiIndex columns when downloading single ticker
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.index = pd.to_datetime(raw.index).date
    raw.index.name = "date"
    return raw


def fetch_prices(
    asset_id: str,
    ticker: str,
    start: datetime.date,
    end: datetime.date,
) -> pd.DataFrame:
    """Return adjusted close prices for asset_id over [start, end].

    Checks DuckDB cache first; fetches missing ranges from yfinance.
    Returns a DataFrame indexed by date with column 'adjusted_close'.
    """
    conn = _get_connection()

    # check what we have in cache
    cached = conn.execute(
        "SELECT date FROM prices_daily WHERE asset_id = ? AND date BETWEEN ? AND ? ORDER BY date",
        [asset_id, start, end],
    ).fetchdf()
    cached_dates = set(cached["date"].dt.date if hasattr(cached["date"], "dt") else cached["date"])

    # determine fetch range: everything in [start, end] not cached
    need_start = start
    need_end = end

    if cached_dates:
        min_cached = min(cached_dates)
        max_cached = max(cached_dates)
        # simple heuristic: if cache covers the full range, skip fetch
        # (gaps in the middle are ignored for prototype simplicity)
        full_start = start >= min_cached
        full_end = end <= max_cached
        if full_start and full_end:
            need_start = None

    if need_start is not None:
        print(f"[prices] fetching {ticker} ({asset_id}) {need_start} → {need_end}", file=sys.stderr)
        raw = _fetch_from_yfinance(ticker, need_start, need_end)
        if not raw.empty:
            rows = []
            for dt, row in raw.iterrows():
                adj_col = "Adj Close" if "Adj Close" in raw.columns else "Close"
                rows.append((
                    asset_id,
                    dt,
                    float(row.get("Open", float("nan"))),
                    float(row.get("High", float("nan"))),
                    float(row.get("Low", float("nan"))),
                    float(row.get("Close", float("nan"))),
                    float(row.get(adj_col, float("nan"))),
                    int(row.get("Volume", 0) or 0),
                    "yfinance",
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)",
                rows,
            )
            print(f"[prices] cached {len(rows)} rows for {asset_id}", file=sys.stderr)

    result = conn.execute(
        """SELECT date, adjusted_close FROM prices_daily
           WHERE asset_id = ? AND date BETWEEN ? AND ?
           ORDER BY date""",
        [asset_id, start, end],
    ).fetchdf()
    conn.close()

    if result.empty:
        raise ValueError(f"No price data found for {asset_id} in {start}:{end}")

    result["date"] = pd.to_datetime(result["date"])
    result = result.set_index("date").sort_index()
    return result
