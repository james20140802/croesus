"""Load the equity universe (prices + sectors) from the source DuckDB.

Prices use ``adjusted_close`` (total-return proxy) exposed under the ``close``
column so downstream factor/return code is agnostic to the raw/adjusted choice.
"""
from __future__ import annotations

import pandas as pd

from experiments.market_signals.cross_sectional.source import connect_source


def load_universe_prices(equities_only: bool = True, min_rows: int = 200) -> dict[str, pd.DataFrame]:
    """Return {asset_id: DataFrame(index=date, cols close, volume)}.

    ``close`` is ``adjusted_close``. Assets with fewer than ``min_rows`` price
    rows are dropped (too short to compute the 200-day factors).
    """
    con = connect_source()
    try:
        where = "a.asset_type = 'equity'" if equities_only else "1 = 1"
        df = con.execute(
            f"""
            SELECT p.asset_id, p.date, p.adjusted_close AS close, p.volume
            FROM prices_daily p
            JOIN assets a ON a.asset_id = p.asset_id
            WHERE {where}
              AND p.adjusted_close IS NOT NULL
              AND p.adjusted_close > 0
            ORDER BY p.asset_id, p.date
            """
        ).fetchdf()
    finally:
        con.close()

    df["date"] = pd.to_datetime(df["date"])
    out: dict[str, pd.DataFrame] = {}
    for aid, g in df.groupby("asset_id"):
        if len(g) >= min_rows:
            out[aid] = g.set_index("date")[["close", "volume"]].sort_index()
    return out


def load_sectors() -> dict[str, str]:
    con = connect_source()
    try:
        rows = con.execute(
            "SELECT asset_id, sector FROM assets WHERE sector IS NOT NULL AND sector <> ''"
        ).fetchall()
    finally:
        con.close()
    return {a: s for a, s in rows}
