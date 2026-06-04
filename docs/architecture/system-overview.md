# System Overview

## Purpose

Croesus is a Python-first personal portfolio management system. Its initial purpose is to build a reliable investor-profile, portfolio, data, and factor pipeline before introducing UI, autonomous agents, or trade execution.

The first interface is the CLI, but the CLI is not the final product boundary.
Croesus should be **CLI-first, app-ready**: business logic lives in reusable
use-case functions and domain modules so a future local web UI, desktop app,
local API, or scheduler can call the same workflows without rewriting the
portfolio engine. See ADR 0009.

The system should not start from "which stock should I buy?" It should start from:

> Given this investor profile and current portfolio, what action should be proposed now, if any?

## High-Level Architecture

```text
Investor Profile
  -> Policy Portfolio
  -> Current Portfolio Snapshot
  -> Portfolio Exposure Analysis

Macro Data Ingestion (FRED, yfinance, scrapers)
  -> Macro Score Engine (3-Layer: Regime / Amplifier / Confirmation)
  -> MacroState ───────────────────────────────────────────────┐
                                                               │ (risk posture)
Asset Universe                                                 ▼
  -> Data Ingestion               Factor Engine -> Screening Engine
  -> Data Store                                    |
                                                   ▼
                                           Candidate Set
                                                   |
                                                   ▼
                                           Research Agent
                                                   |
                                                   ▼
                                      Rebalancing Engine
                                                   |
                                                   ▼
                                      Portfolio Action Report
```

## Components

### App-Ready Runtime Boundary

CLI jobs should parse arguments, call a use-case function, print a concise
summary, and map expected errors to stable exit codes. They should not own the
investment logic.

Expected reusable use-case functions include:

```text
run_profile_init(...)
run_portfolio_snapshot(...)
run_screening_job(...)
run_rebalance_check(...)
run_local_sync(...)
record_transaction(...)
```

Each use case should return structured results that a CLI, local API, scheduler,
or future UI can consume. Markdown and CSV reports are generated from these
structured results; they are not the source of truth.

### 0. Investor Profile

Defines the mandate for portfolio operation.

It includes:

- Expected annual return.
- Maximum tolerable drawdown.
- Investment horizon.
- Contribution and liquidity needs.
- Allowed and disallowed asset classes.
- Concentration limits.
- Rebalancing thresholds.
- Trade mode.

The investor profile is the outer constraint for all portfolio decisions. See `docs/architecture/investor-profile.md`.

### 1. Policy Portfolio

Defines target sleeves and acceptable ranges for the investor profile.

Level 1 may accept policy targets directly instead of deriving them through optimization. For example:

```text
Core US Equity: 55% target
Satellite Equity: 15% target
Defensive / Bonds: 20% target
Cash: 10% target
```

### 2. Current Portfolio

Tracks current holdings, market values, weights, and exposure.

The portfolio layer computes:

- Position weights.
- Sector, industry, theme, country, and currency exposure.
- Cash weight.
- Drift from policy targets.
- Profile constraint violations.

### 3. Macro Score Engine

시장 전체의 거시 환경을 분석하여 `MacroState`를 산출한다.
종목 스크리닝 전에 실행되는 선행 단계로, "현재 포트폴리오의 위험 예산을 늘릴지 줄일지"를 판단한다.

3개 레이어로 구성된다:

- **Layer 1 — Regime**: Growth × Inflation 방향으로 4개 국면 분류 (Goldilocks / Reflation / Stagflation / Deflation).
- **Layer 2 — Risk Amplifier**: 유동성·신용·금리 지표로 국면 내 강도 조정 (0~100점).
- **Layer 3 — Confirmation**: 변동성·추세·심리·FX 지표로 국면 신호 확인 또는 경고 (-1~+1).

`MacroState`는 Screening Engine의 팩터 가중치, 종목 필터 임계값, 후보군 크기, Rebalancing Engine의 risk posture를 조정하는 데 사용된다. MacroState는 profile constraints를 override하지 않는다.

데이터 소스: FRED API, yfinance, 웹 스크래핑(AAII, NAAIM).
갱신 주기: 일간(`daily_macro_run`), 주간(`weekly_macro_run`), 월간(`monthly_macro_run`).

자세한 내용은 `docs/superpowers/specs/2026-05-28-macro-analysis-design.md` 참조.

### 4. Asset Universe

Maintains the list of assets Croesus can analyze.

It should support staged expansion:

- Seed US equities.
- All US-listed equities.
- Global equities.
- ETFs, REITs, bond ETFs, commodities, FX, crypto, and other products.

The system should not depend on hard-coded ticker lists in analysis code.

### 5. Data Ingestion

Collects and normalizes data from external sources.

Initial sources may include:

- Manual seed data for early development.
- yfinance for prototype daily OHLCV data.
- NASDAQ/NYSE/AMEX listings for broader US universe ingestion.
- SEC EDGAR for US filings and company facts.
- News/RSS providers for qualitative research.

Each source should be replaceable.

### 6. Data Store

The MVP should use a local analytical database such as DuckDB.

Core tables:

- `assets`
- `investor_profiles`
- `policy_targets`
- `portfolio_holdings`
- `prices_daily`
- `fundamentals`
- `valuation_snapshots`
- `factor_values`
- `macro_scores`
- `screening_results`
- `reports`

The storage layer should keep raw data, normalized data, and computed factor values separate.

### 7. Factor Engine

Computes deterministic quantitative signals.

Initial common factors:

- 1-month momentum.
- 3-month momentum.
- 6-month momentum.
- 3-month volatility.
- 1-month liquidity.
- 200-day moving-average signal.

Equity-specific factors:

- Valuation: P/E, P/B, EV/EBITDA, FCF yield, DCF intrinsic value, sector percentile ranking.
- Quality, Growth, Profitability, Leverage (future sprints).

Valuation factor outputs go to both `factor_values` (scalar screening metrics) and `valuation_snapshots` (full DCF record). See `docs/superpowers/specs/2026-05-28-valuation-analysis-design.md`.

### 8. Screening Engine

Filters and ranks assets.

Example process:

1. Select a universe.
2. Apply eligibility filters such as asset type, country, liquidity, and market cap.
3. Load factor values.
4. Normalize factor values.
5. Compute strategy score.
6. Save ranked results.

Screening results are candidates, not trade instructions. They must pass investor-profile and current-portfolio constraints before becoming proposed actions.

### 9. Sector and Theme Analysis

Aggregates asset-level signals into sector, industry, and theme exposure.

Initial responsibilities:

- Compute current portfolio exposure by sector, industry, country, currency, and theme.
- Identify concentration risk.
- Block or reduce new buys in overexposed areas.
- Support sector-level overweight/underweight proposals.

### 10. Research Agent

The research agent should run only after quantitative screening has narrowed the candidate set.

It should summarize:

- News.
- Filings.
- Earnings calls.
- Business model.
- Industry dynamics.
- Key risks.

It should not compute core quantitative metrics.

### 11. Rebalancing Engine

Compares the investor profile, policy portfolio, current holdings, MacroState, and candidate assets.

Responsibilities:

- Portfolio drift checks.
- Position sizing constraints.
- Sector exposure checks.
- Industry and theme exposure checks.
- Concentration checks.
- Risk tolerance alignment.
- Rebalancing suggestions.

Level 1 produces proposals only. It should not execute trades.

See `docs/architecture/portfolio-rebalancing.md`.

### 12. Report Generator

Produces human-readable outputs.

Initial report formats:

- Markdown.
- CSV.

Initial report types:

- Macro report.
- Screening report.
- Portfolio action report.

Later report formats:

- Web dashboard.
- PDF.
- Email digest.
- Notion/Google Docs export.

The web dashboard should read persisted state such as snapshots, exposures,
drifts, candidates, proposed actions, transaction history, and freshness status.
It should not scrape CLI text output.

## Initial Runtime Flow

```text
python -m croesus.jobs.bootstrap
python -m croesus.jobs.profile_init
python -m croesus.jobs.portfolio_snapshot
python -m croesus.jobs.daily_run
python -m croesus.jobs.daily_macro_run
python -m croesus.jobs.weekly_macro_run    # 주 1회
python -m croesus.jobs.monthly_macro_run   # 월 1회
python -m croesus.jobs.quarterly_run       # 분기 1회 (valuation layer)
python -m croesus.jobs.rebalance_check     # Level 1 MVP
```

Expected initial behavior:

1. Create or migrate the local DuckDB schema.
2. Create or load an investor profile.
3. Create or load policy portfolio targets.
4. Load current holdings.
5. Seed initial assets.
6. Ingest daily prices.
7. Compute macro scores (MacroState).
8. Compute common factors.
9. Run screening with macro-adjusted parameters.
10. Compare current portfolio against profile and policy constraints.
11. Generate a portfolio action report.

`quarterly_run` in the valuation layer ingests fundamentals via yfinance, then recomputes valuation factors and DCF snapshots.

## Design Constraints

- Do not start with a web app before the portfolio engine is credible, but keep
  all new workflows app-ready.
- Do not start with autonomous trading.
- Do not deeply research every asset with LLMs.
- Do not mix source ingestion, factor computation, and reporting in one module.
- Do not hard-code asset assumptions into factor logic.
- Do not let a screening result become a trade proposal until it passes profile and portfolio constraints.
- Do not let MacroState override investor-profile constraints.
- Do not hide product state only inside Markdown reports or CLI output; persist
  structured state first.

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

  profiles/
    models.py
    repository.py
    validation.py
    seed_default_profile.py

  portfolio/
    holdings.py
    policy.py
    exposure.py
    rebalancing.py

  data_sources/
    base.py
    yfinance_source.py
    fundamentals/
      base.py
      yfinance_fundamentals.py

  prices/
    ingest_prices.py
    repository.py

  fundamentals/
    ingest_fundamentals.py
    repository.py

  factors/
    common.py
    compute_common_factors.py
    equity/
      valuation.py

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
    portfolio_action.py

  jobs/
    bootstrap.py
    profile_init.py
    portfolio_snapshot.py
    daily_run.py
    daily_macro_run.py
    weekly_macro_run.py
    monthly_macro_run.py
    quarterly_run.py
    rebalance_check.py
```
