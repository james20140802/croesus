# Screening Universe — S&P 500 + NASDAQ-100 Refresh

Sprint 008c replaces the ~15-ticker seed universe with the real index
constituents (~600 names, ~516 unique after cross-index dedup), refreshed
weekly.

## How it works

1. `croesus/assets/universe_sources/wikipedia.py` reads the constituent tables
   from the Wikipedia S&P 500 and NASDAQ-100 list pages with
   `pandas.read_html` — no API key, and parsing is column-name driven
   (`Symbol`/`Ticker` + `Security`/`Company`) so the table can move on the
   page without breaking the fetch.
2. `croesus/assets/ingest_universe.py` dedups symbols that sit in both indices
   (AAPL → one row with `metadata.indices = ["nasdaq100", "sp500"]`),
   normalizes share-class dots to the yfinance dash form (`BRK.B` → `BRK-B`,
   asset id `US_EQ_BRK_B`), and upserts idempotently. Existing rows only get
   missing fields filled in — `manual_seed` assets keep their curated name,
   sector, and source.
3. `python -m croesus.jobs.universe_refresh` runs the ingestion. It is
   registered in `local_sync` under the new `asset_universe` freshness domain
   (weekly threshold), ordered **before** `daily_run` so newly registered
   names get their 1-year price backfill — enough history for every common
   factor — in the same sync cycle.

## Failure policy (Sprint 008a integrity contract)

- One index failing degrades **loudly**: the other index still lands, a
  warn-level `UNIVERSE_SOURCE_FAILED` row is written to
  `data_quality_issues`, and the job summary names the failed source.
- All sources failing raises `UniverseRefreshError`: the sync run records a
  failure and the domain stays due, so the refresh is retried next cycle.

## Honest limitations

- Wikipedia reflects **current** membership only. Names that leave an index
  stay registered and active (they may be held), but historical constituents
  are not recovered — this is the survivorship caveat the Sprint 014 backtest
  must state on every report.
- A handful of rows lack a GICS sector on the source page; enrichment stays
  lazy via the asset resolver (registering ~600 names must not mean ~600
  yfinance calls).
- The first `daily_run` after a fresh refresh fetches 1y of prices for every
  new name and takes correspondingly longer; subsequent runs are incremental
  in effect (upserts over mostly-existing rows).

## Manual verification

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("SELECT COUNT(*) FROM assets WHERE source = 'universe_index'").df())
    print(conn.execute(
        "SELECT asset_id, symbol, metadata FROM assets WHERE symbol = 'AAPL'"
    ).df())
```
