"""Daily index prices via a DuckDB read-through cache.

Shares storage/croesus.duckdb and the prices_daily table with the rest of the
repo. Self-contained (no cross-package imports) so it runs from the repo root.
"""
import datetime
import sys

import duckdb
import pandas as pd
import yfinance as yf

from experiments.market_signals.common.config import DB_PATH

INDICES = {"US_IDX_SP500": "^GSPC", "US_IDX_NASDAQ": "^IXIC"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_daily (
    asset_id TEXT, date DATE, open DOUBLE, high DOUBLE, low DOUBLE,
    close DOUBLE, adjusted_close DOUBLE, volume BIGINT, source TEXT,
    PRIMARY KEY (asset_id, date)
)
"""


def _connect() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(_SCHEMA)
    return conn


def _fetch(ticker: str, start: datetime.date, end: datetime.date) -> pd.DataFrame:
    raw = yf.download(
        ticker, start=str(start), end=str(end + datetime.timedelta(days=1)),
        auto_adjust=False, progress=False,
    )
    if raw.empty:
        return raw
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    raw.index = pd.to_datetime(raw.index).date
    return raw


def load_prices(asset_id: str, ticker: str,
                start: datetime.date, end: datetime.date) -> pd.DataFrame:
    conn = _connect()
    try:
        cached = conn.execute(
            "SELECT date FROM prices_daily WHERE asset_id=? AND date BETWEEN ? AND ?",
            [asset_id, start, end],
        ).fetchdf()
        have = set(cached["date"].dt.date if hasattr(cached["date"], "dt") else cached["date"])

        covered = bool(have) and start >= min(have) and end <= max(have)
        if not covered:
            print(f"[data] fetching {ticker} {start}->{end}", file=sys.stderr)
            raw = _fetch(ticker, start, end)
            if not raw.empty:
                adj = "Adj Close" if "Adj Close" in raw.columns else "Close"
                rows = [(
                    asset_id, dt,
                    float(r.get("Open", float("nan"))), float(r.get("High", float("nan"))),
                    float(r.get("Low", float("nan"))), float(r.get("Close", float("nan"))),
                    float(r.get(adj, float("nan"))), int(r.get("Volume", 0) or 0), "yfinance",
                ) for dt, r in raw.iterrows()]
                conn.executemany(
                    "INSERT OR REPLACE INTO prices_daily VALUES (?,?,?,?,?,?,?,?,?)", rows)

        out = conn.execute(
            """SELECT date, adjusted_close FROM prices_daily
               WHERE asset_id=? AND date BETWEEN ? AND ? ORDER BY date""",
            [asset_id, start, end],
        ).fetchdf()
        if out.empty:
            raise ValueError(f"No price data for {asset_id} in {start}:{end}")
        out["date"] = pd.to_datetime(out["date"])
        return out.set_index("date").sort_index()
    finally:
        conn.close()
