# Sprint 001: Asset Registry and US Equity Price Ingestion

## Goal

Build the first working Croesus pipeline:

```text
asset registry
  -> daily price ingestion
  -> common factor computation
  -> basic output verification
```

The goal is not to build a full investment agent yet. The goal is to create the core data foundation that later screening, research, and portfolio modules can rely on.

## Scope

### 1. Project setup

- Create Python package structure.
- Add dependency management with `uv` or `poetry`.
- Add basic README instructions.
- Add `.env.example` if needed.

Suggested dependencies:

```text
duckdb
pandas
yfinance
pydantic
pyyaml
python-dotenv
```

### 2. Database setup

Create:

```text
croesus/db/connection.py
croesus/db/schema.sql
croesus/db/migrate.py
```

Initial tables:

- `assets`
- `prices_daily`
- `factor_values`
- `screening_results`

### 3. Asset registry

Create:

```text
croesus/assets/models.py
croesus/assets/repository.py
croesus/assets/seed_us_equities.py
```

Initial manual seed assets:

```text
AAPL
MSFT
NVDA
```

These are not the final universe. They are only for validating the pipeline.

### 4. Price ingestion

Create:

```text
croesus/data_sources/base.py
croesus/data_sources/yfinance_source.py
croesus/prices/ingest_prices.py
croesus/prices/repository.py
```

Initial behavior:

- Read active US equities from `assets`.
- Download 1 year of daily OHLCV data.
- Store rows in `prices_daily`.
- Continue if one ticker fails.
- Print clear success/skip logs.

### 5. Common factor computation

Create:

```text
croesus/factors/common.py
croesus/factors/compute_common_factors.py
```

Initial factors:

- `momentum_1m`
- `momentum_3m`
- `momentum_6m`
- `volatility_3m`
- `liquidity_1m`
- `above_200d_ma`

Store results in `factor_values`.

### 6. Job entrypoints

Create:

```text
croesus/jobs/bootstrap.py
croesus/jobs/daily_run.py
```

Expected commands:

```bash
python -m croesus.jobs.bootstrap
python -m croesus.jobs.daily_run
```

## Out of Scope

- Full US equity universe ingestion.
- Web UI.
- LLM research.
- Portfolio optimization.
- Brokerage integration.
- Automatic trading.
- Backtesting.
- SEC fundamental ingestion.
- News crawling.

## Acceptance Criteria

### Bootstrap

`python -m croesus.jobs.bootstrap` should:

- Create local DuckDB storage.
- Apply schema.
- Insert seed assets into `assets`.

### Daily run

`python -m croesus.jobs.daily_run` should:

- Load active seed assets.
- Download daily price data.
- Store data in `prices_daily`.
- Compute common factors.
- Store factors in `factor_values`.
- Complete even if one asset fails.

### Manual verification

A developer should be able to run:

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("SELECT * FROM assets").df())
    print(conn.execute("SELECT * FROM prices_daily LIMIT 5").df())
    print(conn.execute("SELECT * FROM factor_values").df())
```

Expected result:

- `assets` contains AAPL, MSFT, NVDA.
- `prices_daily` contains OHLCV data.
- `factor_values` contains common factor values for assets with enough history.

## Suggested PR Title

```text
feat: add asset registry and US equity price ingestion
```

## Suggested Commit Breakdown

```text
chore: initialize python project
feat: add duckdb schema and migration
feat: add asset registry seed
feat: add yfinance price ingestion
feat: compute common factors
```

## Notes

Do not over-engineer multi-agent workflows in this sprint. The first priority is a small, testable, deterministic pipeline.
