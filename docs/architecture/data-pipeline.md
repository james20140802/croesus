# Data Pipeline

## Purpose

The data pipeline collects, normalizes, stores, and updates the information needed by Croesus.

The pipeline should be reliable before it is sophisticated. The first version should focus on a small number of assets and a small number of data types, then expand gradually.

## Pipeline Stages

```text
Source Discovery
  -> Data Download
  -> Normalization
  -> Validation
  -> Storage
  -> Factor Computation
  -> Screening
  -> Report Generation
```

## Initial Data Types

### 1. Asset metadata

Stored in `assets`.

Examples:

- Symbol.
- Name.
- Asset type.
- Exchange.
- Country.
- Currency.
- Sector.
- Industry.
- Source.

### 2. Daily prices

Stored in `prices_daily`.

Examples:

- Date.
- Open.
- High.
- Low.
- Close.
- Adjusted close.
- Volume.
- Source.

### 3. Fundamentals

Stored in `fundamentals` later.

Examples:

- Revenue.
- Net income.
- EPS.
- Book value.
- Free cash flow.
- Debt.
- Shares outstanding.

### 4. Factor values

Stored in `factor_values`.

Examples:

- Momentum.
- Volatility.
- Liquidity.
- Valuation.
- Quality.
- Growth.

### 5. Qualitative research data

Stored later in separate tables or document stores.

Examples:

- News.
- Filings.
- Earnings call transcripts.
- Analyst summaries.
- Company descriptions.

## Initial Storage Choice

Use DuckDB for the MVP.

Reasons:

- Simple local setup.
- Good analytical query support.
- Works well with pandas.
- Easy to version schema SQL.
- Suitable for research prototypes.

## Initial Tables

### assets

```sql
CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL,
  name TEXT,
  asset_type TEXT NOT NULL,
  country TEXT,
  exchange TEXT,
  currency TEXT,
  sector TEXT,
  industry TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  source TEXT,
  metadata JSON
);
```

### prices_daily

```sql
CREATE TABLE IF NOT EXISTS prices_daily (
  asset_id TEXT NOT NULL,
  date DATE NOT NULL,
  open DOUBLE,
  high DOUBLE,
  low DOUBLE,
  close DOUBLE,
  adjusted_close DOUBLE,
  volume BIGINT,
  source TEXT,
  PRIMARY KEY (asset_id, date)
);
```

### factor_values

```sql
CREATE TABLE IF NOT EXISTS factor_values (
  asset_id TEXT NOT NULL,
  date DATE NOT NULL,
  factor_name TEXT NOT NULL,
  value DOUBLE,
  PRIMARY KEY (asset_id, date, factor_name)
);
```

### screening_results

```sql
CREATE TABLE IF NOT EXISTS screening_results (
  run_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  score DOUBLE,
  rank INTEGER,
  decision_bucket TEXT,
  reason TEXT,
  PRIMARY KEY (run_id, asset_id)
);
```

## Source Interface

Each source should be wrapped behind an interface. Downstream logic should not depend directly on source-specific response formats.

Example source modules:

```text
data_sources/
  base.py
  yfinance_source.py
  nasdaq_source.py
  sec_source.py
```

## MVP Data Source Plan

### Phase 1

- Manual seed for assets.
- yfinance for daily prices.

### Phase 2

- NASDAQ/NYSE/AMEX listed-symbol ingestion.
- yfinance or another provider for expanded daily prices.

### Phase 3

- SEC EDGAR for company metadata and fundamentals.
- News/RSS ingestion for qualitative research.

### Phase 4

- Paid or higher-quality providers if the free data layer becomes insufficient.

## Validation Rules

The ingestion pipeline should validate:

- Asset IDs are unique.
- Symbols are non-empty.
- Asset types are recognized.
- Price rows have valid dates.
- OHLC values are non-negative.
- Volume is non-negative.
- Duplicate `(asset_id, date)` rows are replaced or ignored deterministically.
- Missing data is logged.

## Update Pattern

Initial jobs:

```text
bootstrap
  -> migrate schema
  -> seed initial assets

daily_run
  -> ingest prices
  -> compute common factors
  -> run screening
  -> generate report
```

## Data Provenance

Every externally collected value should keep a `source` field when practical.

This matters because different data providers may disagree on:

- Adjusted prices.
- Shares outstanding.
- Sector classification.
- ETF classification.
- Delisting status.
- Fundamentals restatements.

## Separation of Concerns

Do not mix these responsibilities:

- Source download.
- Normalization.
- Storage.
- Factor computation.
- Screening.
- Reporting.

Each should be a separate module so the system can scale from a toy prototype to a broad research pipeline.
