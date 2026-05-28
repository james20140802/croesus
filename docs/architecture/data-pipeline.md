# Data Pipeline

## Purpose

The data pipeline collects, normalizes, stores, and updates the information needed by Croesus.

The pipeline should be reliable before it is sophisticated. The first version should focus on a small number of assets and a small number of data types, then expand gradually.

## Pipeline Stages

```text
[Macro Pipeline]
Macro Source Download (FRED / yfinance / scrapers)
  -> Normalization
  -> Macro Score Computation (3-Layer)
  -> MacroState Storage
  -> Macro Report Generation

[Asset Pipeline]
Source Discovery
  -> Data Download
  -> Normalization
  -> Validation
  -> Storage
  -> Factor Computation
  -> Screening (MacroState로 파라미터 조정)
  -> Research Report Generation
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

Stored in `fundamentals`. Ingested quarterly via `FundamentalsProvider` (Sprint 003).

Long-format schema consistent with `factor_values`:

```sql
CREATE TABLE IF NOT EXISTS fundamentals (
  asset_id     TEXT NOT NULL,
  period_end   DATE NOT NULL,
  period_type  TEXT NOT NULL,   -- 'annual' | 'quarterly'
  metric_name  TEXT NOT NULL,
  value        DOUBLE,
  source       TEXT,
  PRIMARY KEY (asset_id, period_end, period_type, metric_name)
);
```

Initial `metric_name` values: `revenue`, `operating_income`, `net_income`, `eps`,
`free_cash_flow`, `total_debt`, `total_equity`, `cash_and_equivalents`,
`shares_outstanding`, `ebitda`, `capex`, `book_value_per_share`.

### 4. Factor values

Stored in `factor_values`.

Examples:

- Momentum.
- Volatility.
- Liquidity.
- Valuation.
- Quality.
- Growth.

### 5. Macro indicators

Stored in `macro_scores`.

Sources:

- FRED API: 금리, 인플레이션, 고용, 유동성, 신용 스프레드 등 (무료).
- yfinance: VIX, S&P 500, DXY, 원자재(구리·금·WTI) (무료).
- 웹 스크래핑: AAII Sentiment, NAAIM Exposure (무료, 불안정 가능).

갱신 주기:

- 일간: VIX, 금리, Credit Spread, RRP, S&P 500, FX, 원자재.
- 주간: AAII, NAAIM, Jobless Claims, Fed Balance Sheet, TGA.
- 월간: CPI, PCE, PMI, GDP, 실업률, M2, 임금상승률.

### 6. Qualitative research data

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

### macro_scores

```sql
CREATE TABLE IF NOT EXISTS macro_scores (
  date                DATE PRIMARY KEY,
  regime              TEXT NOT NULL,
  regime_confidence   DOUBLE,
  growth_direction    TEXT,
  inflation_direction TEXT,
  amplifier_score     DOUBLE,
  confirmation_score  DOUBLE,
  positioning         TEXT,
  raw_indicators      JSON,
  warnings            JSON,
  opportunities       JSON
);
```

### fundamentals

See Section 3 above for the full schema.

### valuation_snapshots

DCF output and assumptions record. One row per asset per date.

```sql
CREATE TABLE IF NOT EXISTS valuation_snapshots (
  asset_id                  TEXT NOT NULL,
  date                      DATE NOT NULL,
  intrinsic_value_per_share DOUBLE,
  current_price             DOUBLE,
  upside_pct                DOUBLE,
  wacc                      DOUBLE,
  fcf_growth_rate           DOUBLE,
  terminal_growth_rate      DOUBLE,
  assumptions_json          TEXT,
  PRIMARY KEY (asset_id, date)
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

### Phase 2 (Sprint 002)

- FRED API for macro indicators (금리·인플레·고용·유동성·신용).
- yfinance for macro indicators (VIX, S&P 500, FX, 원자재).
- AAII, NAAIM 웹 스크래핑 for sentiment.
- Macro Score Engine 구현 및 `macro_scores` 테이블 저장.

### Phase 3 (Sprint 003)

- yfinance for quarterly fundamentals (income statement, balance sheet, cash flow).
- `fundamentals` 테이블 저장.
- Valuation factor computation: P/E, P/B, EV/EBITDA, FCF yield, sector percentiles.
- 2-stage DCF with CAPM WACC → `valuation_snapshots` 테이블 저장.
- `quarterly_run` 잡 추가.

### Phase 4

- NASDAQ/NYSE/AMEX listed-symbol ingestion.
- yfinance or another provider for expanded daily prices.

### Phase 5

- SEC EDGAR for company metadata and higher-quality fundamentals.
- News/RSS ingestion for qualitative research.

### Phase 6

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

```text
bootstrap
  -> migrate schema (macro_scores 테이블 포함)
  -> seed initial assets

daily_macro_run         ← 매일
  -> ingest daily macro indicators (FRED, yfinance)
  -> compute MacroState
  -> store macro_scores
  -> generate macro report

weekly_macro_run        ← 주 1회
  -> ingest weekly indicators (AAII, NAAIM, Jobless Claims, TGA)
  -> update MacroState

monthly_macro_run       ← 월 1회
  -> ingest monthly indicators (CPI, PCE, PMI, GDP, 실업률, M2)
  -> update MacroState

daily_run               ← 매일 (daily_macro_run 이후 실행)
  -> ingest prices
  -> compute common factors
  -> compute valuation multiples (pe_ratio, pb_ratio 등, fundamentals 캐시 사용)
  -> run screening (MacroState로 파라미터 조정)
  -> generate research report

quarterly_run           ← 분기 1회 (Sprint 003+)
  -> fetch fundamentals via FundamentalsProvider (yfinance)
  -> store in fundamentals table
  -> recompute DCF → store in valuation_snapshots
  -> update price_to_intrinsic in factor_values
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
