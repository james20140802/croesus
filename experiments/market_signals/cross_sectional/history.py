"""Long-history price cache for the cross-sectional experiment.

The production DB truncates equity history at ~2016 (the ingest pulled only ~10y).
To test whether the beta/volatility result is just a single-bull-market artifact,
we fetch full history (default from 1990) for the *current* universe symbols via
yfinance into a **separate scratch DuckDB** — production is never touched.

Caveat this does NOT fix: survivorship bias. We still use today's surviving
tickers, so delisted/failed names are absent. Longer history adds bear regimes
(2000, 2008, 2020, 2022) but the universe is still the set of survivors.

Usage:
  python -m experiments.market_signals.cross_sectional.history          # fetch
  python -m experiments.market_signals.cross_sectional.history --status # coverage
"""
from __future__ import annotations

import sys
import time

import duckdb
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.source import connect_source

SCRATCH_DB = RESULTS_DIR / "cross_sectional" / "long_history.duckdb"
FETCH_START = "1990-01-01"
CHUNK = 40

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices_long (
    asset_id TEXT, date DATE, close DOUBLE, volume BIGINT,
    PRIMARY KEY (asset_id, date)
)
"""


def _connect_scratch() -> duckdb.DuckDBPyConnection:
    SCRATCH_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(SCRATCH_DB))
    con.execute(_SCHEMA)
    return con


def _symbol_map() -> dict[str, str]:
    """asset_id -> yfinance-friendly symbol (class shares use '-')."""
    con = connect_source()
    try:
        rows = con.execute(
            "SELECT asset_id, symbol FROM assets WHERE asset_type='equity' AND symbol IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {aid: sym for aid, sym in rows}


def fetch_all(start: str = FETCH_START) -> None:
    import yfinance as yf

    con = _connect_scratch()
    have = {r[0] for r in con.execute("SELECT DISTINCT asset_id FROM prices_long").fetchall()}
    symbols = {a: s for a, s in _symbol_map().items() if a not in have}
    print(f"[history] {len(have)} already cached; fetching {len(symbols)} symbols from {start}",
          flush=True)

    items = list(symbols.items())
    fetched = 0
    for i in range(0, len(items), CHUNK):
        batch = items[i:i + CHUNK]
        # yfinance uses '-' for class shares (BRK.B -> BRK-B)
        yf_syms = {aid: sym.replace(".", "-") for aid, sym in batch}
        tickers = list(dict.fromkeys(yf_syms.values()))
        try:
            raw = yf.download(tickers, start=start, end="2026-06-30", auto_adjust=False,
                              group_by="ticker", progress=False, threads=True)
        except Exception as e:  # whole-batch failure: skip, continue
            print(f"[history] batch {i}-{i+len(batch)} download error: {e}", flush=True)
            continue

        rows = []
        for aid, sym in yf_syms.items():
            try:
                sub = raw[sym] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            sub = sub.dropna(subset=["Adj Close"]) if "Adj Close" in sub.columns else sub.dropna()
            if sub.empty:
                continue
            adj = "Adj Close" if "Adj Close" in sub.columns else "Close"
            for dt, r in sub.iterrows():
                c = r.get(adj)
                if pd.isna(c) or float(c) <= 0:
                    continue
                rows.append((aid, pd.Timestamp(dt).date(), float(c), int(r.get("Volume", 0) or 0)))
        if rows:
            con.executemany("INSERT OR REPLACE INTO prices_long VALUES (?,?,?,?)", rows)
        fetched += len(batch)
        print(f"[history] {fetched}/{len(items)} symbols processed "
              f"(+{len(rows)} rows this batch)", flush=True)
        time.sleep(0.5)
    con.close()
    print("[history] done", flush=True)


def load_long_history(min_rows: int = 200, start_year: int = 1995) -> dict[str, pd.DataFrame]:
    """Read the scratch DB into {asset_id: DataFrame(index=date, close, volume)}."""
    con = _connect_scratch()
    try:
        df = con.execute(
            "SELECT asset_id, date, close, volume FROM prices_long "
            "WHERE YEAR(date) >= ? ORDER BY asset_id, date",
            [start_year],
        ).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    out: dict[str, pd.DataFrame] = {}
    for aid, g in df.groupby("asset_id"):
        if len(g) >= min_rows:
            out[aid] = g.set_index("date")[["close", "volume"]].sort_index()
    return out


def status() -> None:
    con = _connect_scratch()
    try:
        n = con.execute("SELECT COUNT(*) FROM prices_long").fetchone()[0]
        a = con.execute("SELECT COUNT(DISTINCT asset_id) FROM prices_long").fetchone()[0]
        rng = con.execute("SELECT MIN(date), MAX(date) FROM prices_long").fetchone()
    finally:
        con.close()
    print(f"[history] {a} assets, {n:,} rows, {rng[0]}..{rng[1]}")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
    else:
        fetch_all()
