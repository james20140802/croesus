"""Price series for the vol-targeting experiment (로드맵 ②).

Assets:
  * SPY — fetched once from yfinance (1993~, Adj Close = total return) into a
    scratch DuckDB under results/. Production DB is never touched.
  * EW  — equal-weight daily portfolio of the cross-sectional long-history
    universe (523 survivors, 1990~). Survivorship inflates its return LEVEL,
    but the vol-targeting comparison is internal (same portfolio, scaled
    exposure), so the overlay-vs-B&H comparison stays fair.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.history import load_long_history

SCRATCH_DB = RESULTS_DIR / "vol_targeting" / "index_history.duckdb"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_prices (
    symbol TEXT, date DATE, adj_close DOUBLE,
    PRIMARY KEY (symbol, date)
)
"""


def _connect() -> duckdb.DuckDBPyConnection:
    SCRATCH_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(SCRATCH_DB))
    con.execute(_SCHEMA)
    return con


def fetch_spy(start: str = "1993-01-29") -> None:
    """One-time SPY download into the scratch cache (no-op when cached)."""
    import yfinance as yf

    con = _connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM index_prices WHERE symbol='SPY'").fetchone()[0]
        if n > 1000:
            return
        raw = yf.download("SPY", start=start, end="2026-06-30",
                          auto_adjust=False, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.dropna(subset=["Adj Close"])
        rows = [("SPY", pd.Timestamp(dt).date(), float(v))
                for dt, v in raw["Adj Close"].items() if float(v) > 0]
        con.executemany("INSERT OR REPLACE INTO index_prices VALUES (?,?,?)", rows)
        print(f"[data] cached {len(rows)} SPY rows", flush=True)
    finally:
        con.close()


def load_spy() -> pd.Series:
    con = _connect()
    try:
        df = con.execute(
            "SELECT date, adj_close FROM index_prices WHERE symbol='SPY' ORDER BY date"
        ).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["adj_close"].rename("close")


def equal_weight_returns(prices: dict[str, pd.DataFrame], min_names: int = 30) -> pd.Series:
    """Daily equal-weight mean return across all names with data that day."""
    wide = pd.DataFrame({aid: df["close"].pct_change() for aid, df in prices.items()})
    counts = wide.notna().sum(axis=1)
    return wide.mean(axis=1)[counts >= min_names].dropna().rename("ew_ret")


def load_ew_returns(start_year: int = 1990) -> pd.Series:
    return equal_weight_returns(load_long_history(start_year=start_year))
