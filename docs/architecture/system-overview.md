# System Overview

## Purpose

Croesus is a Python-first investment research system. Its initial purpose is to build a reliable data and factor pipeline before introducing UI, autonomous agents, or trade execution.

## High-Level Architecture

```text
Macro Data Ingestion (FRED, yfinance, scrapers)
  -> Macro Score Engine (3-Layer: Regime / Amplifier / Confirmation)
  -> MacroState ──────────────────────────────────┐
                                                   │ (파라미터 조정)
Asset Universe                                     ▼
  -> Data Ingestion               Factor Engine -> Screening Engine
  -> Data Store                                    |
                                                   ▼
                                           Research Agent
                                                   |
                                                   ▼
                                           Portfolio Engine
                                                   |
                                                   ▼
                                           Report Generator
```

## Components

### 0. Macro Score Engine

시장 전체의 거시 환경을 분석하여 `MacroState`를 산출한다.
종목 스크리닝 전에 실행되는 선행 단계로, "지금 주식 시장에 투자해도 되는가?"를 판단한다.

3개 레이어로 구성된다:

- **Layer 1 — Regime**: Growth × Inflation 방향으로 4개 국면 분류 (Goldilocks / Reflation / Stagflation / Deflation).
- **Layer 2 — Risk Amplifier**: 유동성·신용·금리 지표로 국면 내 강도 조정 (0~100점).
- **Layer 3 — Confirmation**: 변동성·추세·심리·FX 지표로 국면 신호 확인 또는 경고 (-1~+1).

`MacroState`는 Screening Engine의 팩터 가중치, 종목 필터 임계값, 후보군 크기를 조정하는 데 사용된다.

데이터 소스: FRED API, yfinance, 웹 스크래핑(AAII, NAAIM).
갱신 주기: 일간(`daily_macro_run`), 주간(`weekly_macro_run`), 월간(`monthly_macro_run`).

자세한 내용은 `docs/superpowers/specs/2026-05-28-macro-analysis-design.md` 참조.

### 1. Asset Universe

Maintains the list of assets Croesus can analyze.

It should support staged expansion:

- Seed US equities.
- All US-listed equities.
- Global equities.
- ETFs, REITs, bond ETFs, commodities, FX, crypto, and other products.

The system should not depend on hard-coded ticker lists in analysis code.

### 2. Data Ingestion

Collects and normalizes data from external sources.

Initial sources may include:

- Manual seed data for early development.
- yfinance for prototype daily OHLCV data.
- NASDAQ/NYSE/AMEX listings for broader US universe ingestion.
- SEC EDGAR for US filings and company facts.
- News/RSS providers for qualitative research.

Each source should be replaceable.

### 3. Data Store

The MVP should use a local analytical database such as DuckDB.

Core tables:

- `assets`
- `prices_daily`
- `fundamentals`
- `factor_values`
- `macro_scores`
- `screening_results`
- `reports`

The storage layer should keep raw data, normalized data, and computed factor values separate.

### 4. Factor Engine

Computes deterministic quantitative signals.

Initial common factors:

- 1-month momentum.
- 3-month momentum.
- 6-month momentum.
- 3-month volatility.
- 1-month liquidity.
- 200-day moving-average signal.

Later equity-specific factors:

- Valuation.
- Quality.
- Growth.
- Profitability.
- Leverage.

### 5. Screening Engine

Filters and ranks assets.

Example process:

1. Select a universe.
2. Apply eligibility filters such as asset type, country, liquidity, and market cap.
3. Load factor values.
4. Normalize factor values.
5. Compute strategy score.
6. Save ranked results.

### 6. Research Agent

The research agent should run only after quantitative screening has narrowed the candidate set.

It should summarize:

- News.
- Filings.
- Earnings calls.
- Business model.
- Industry dynamics.
- Key risks.

It should not compute core quantitative metrics.

### 7. Portfolio Engine

Compares screened candidates against the user's current portfolio.

Responsibilities:

- Position sizing constraints.
- Sector exposure checks.
- Concentration checks.
- Risk tolerance alignment.
- Rebalancing suggestions.

The portfolio engine should not execute trades without explicit approval.

### 8. Report Generator

Produces human-readable outputs.

Initial report formats:

- Markdown.
- CSV.

Later report formats:

- Web dashboard.
- PDF.
- Email digest.
- Notion/Google Docs export.

## Initial Runtime Flow

```text
python -m croesus.jobs.bootstrap
python -m croesus.jobs.daily_run
python -m croesus.jobs.daily_macro_run
python -m croesus.jobs.weekly_macro_run   # 주 1회
python -m croesus.jobs.monthly_macro_run  # 월 1회
```

Expected initial behavior:

1. Create or migrate the local DuckDB schema.
2. Seed initial assets.
3. Ingest daily prices.
4. Compute macro scores (MacroState).
5. Compute common factors.
6. Run screening with macro-adjusted parameters.
7. Generate screening and macro research outputs.

## Design Constraints

- Do not start with a web app.
- Do not start with autonomous trading.
- Do not deeply research every asset with LLMs.
- Do not mix source ingestion, factor computation, and reporting in one module.
- Do not hard-code asset assumptions into factor logic.

## Recommended Initial Repository Shape

```text
croesus/
  db/
    connection.py
    schema.sql
    migrate.py

  assets/
    models.py
    repository.py
    seed_us_equities.py

  data_sources/
    base.py
    yfinance_source.py

  prices/
    ingest_prices.py
    repository.py

  factors/
    common.py
    compute_common_factors.py

  macro/
    data_sources/
      fred_source.py
      yfinance_macro.py
      sentiment_scraper.py
    indicators/
      growth.py
      inflation.py
      amplifier.py
      confirmation.py
    engine.py
    screening_adapter.py
    report.py
    templates.py

  screening/
    run_screening.py

  reports/
    markdown.py

  jobs/
    bootstrap.py
    daily_run.py
    daily_macro_run.py
    weekly_macro_run.py
    monthly_macro_run.py
```
