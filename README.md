# Croesus

Python-first investment research pipeline.

## Setup

This repo uses `uv` for dependency management.

```bash
uv sync
```

Optional local configuration:

```bash
cp .env.example .env
```

By default, Croesus stores local data at `storage/croesus.duckdb`.

## Sprint 001 Pipeline

Create the DuckDB schema and seed the initial US equity assets:

```bash
python -m croesus.jobs.bootstrap
```

Run the daily pipeline:

```bash
python -m croesus.jobs.daily_run
```

The daily run reads active US equities from the asset registry, downloads one
year of daily OHLCV data from yfinance, stores prices, and computes common
deterministic factors.

## Manual Verification

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("SELECT * FROM assets").df())
    print(conn.execute("SELECT * FROM prices_daily LIMIT 5").df())
    print(conn.execute("SELECT * FROM factor_values").df())
```

## Tests

```bash
python -m pytest
```
