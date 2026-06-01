# Profile-First Portfolio Roadmap

## Purpose

This roadmap reorganizes Croesus around the new product direction:

> Croesus is a personal portfolio management system that operates from an explicit investor profile and produces deterministic rebalancing proposals.

The target for MVP Level 1 is:

```text
Investor Profile
  -> Policy Portfolio
  -> Current Holdings
  -> Macro / Factor / Exposure Analysis
  -> Rebalancing Proposal
```

Level 1 does not execute trades.

## Sprint Overview

| Sprint | Name | Outcome |
|---|---|---|
| 001 | Data foundation | Asset registry, prices, common factors |
| 002 | Macro risk posture | MacroState and macro-adjusted screening params |
| 003 | Investor profile and policy portfolio | Advanced profile input, validation, policy targets |
| 004 | Portfolio snapshot and exposure | Holdings, weights, drift, concentration checks |
| 004b | Portfolio mark-to-market and FX | Current-price valuation, multi-currency FX, unrealized P&L |
| 005 | Screening and sector/theme analysis | Candidate ranking plus sector/theme exposure inputs |
| 006 | Rebalancing proposal engine | Level 1 MVP: deterministic portfolio action report |
| 007 | Valuation layer | Fundamentals, valuation factors, DCF snapshots |
| 008 | Research Agent | LLM qualitative research for shortlisted candidates |
| 009 | Approval-based execution | Prepare orders after explicit user approval |
| 010 | Bounded automation | Long-horizon automated rebalancing with strict guardrails |

Valuation work remains important, but portfolio-profile infrastructure should come first so valuation outputs have a portfolio decision context.

Sprint 004b is a follow-on to Sprint 004 that promotes its deferred items (quantity-only valuation, multi-currency FX) into current-price mark-to-market with unrealized P&L. It is sequenced before Sprint 006 (rebalancing) because rebalancing needs accurate current portfolio values. It is distinct from Sprint 007 (equity fundamental valuation / DCF). See `sprint-004b-portfolio-mark-to-market-fx.md`.

## Sprint 001: Data Foundation

### Goal

Build the deterministic data foundation:

```text
Asset Registry
  -> Daily Price Ingestion
  -> Common Factor Computation
```

### Scope

- Python package structure.
- DuckDB schema and migrations.
- `assets`, `prices_daily`, `factor_values`, `screening_results`.
- Seed assets: AAPL, MSFT, NVDA.
- yfinance daily OHLCV ingestion.
- Common factors:
  - `momentum_1m`
  - `momentum_3m`
  - `momentum_6m`
  - `volatility_3m`
  - `liquidity_1m`
  - `above_200d_ma`
- `bootstrap` and `daily_run`.

### Acceptance Criteria

- `python -m croesus.jobs.bootstrap` creates schema and seed assets.
- `python -m croesus.jobs.daily_run` stores prices and common factors.
- One failed asset does not crash the whole run.

## Sprint 002: Macro Risk Posture

### Goal

Compute MacroState and connect it to the daily pipeline as a risk-posture input.

```text
Macro Data
  -> Regime / Amplifier / Confirmation
  -> MacroState
  -> Screening Params
```

### Scope

- `macro_scores` schema.
- FRED source.
- yfinance macro source.
- ISM scraper with CFNAI fallback.
- AAII and NAAIM scrapers where feasible.
- Growth and inflation direction modules.
- Risk amplifier.
- Confirmation score.
- `MacroState`.
- Multi-method regime cross-validation for report output only.
- `load_latest_macro_state()`.
- `daily_run` consumes latest MacroState and outputs screening params.

### Acceptance Criteria

- `python -m croesus.jobs.daily_macro_run` stores a valid MacroState.
- `python -m croesus.jobs.daily_run` can load latest MacroState.
- Missing macro data falls back to neutral params.
- MacroState does not directly select trades or override investor-profile constraints.

## Sprint 003: Investor Profile and Policy Portfolio

### Goal

Introduce the profile-first mandate that all future portfolio actions must obey.

```text
Investor Profile
  -> Profile Validation
  -> Policy Portfolio Targets
```

### Scope

- Add `investor_profiles` table.
- Add `policy_targets` table.
- Add profile model.
- Add profile repository.
- Add profile validation.
- Add default profile seed.
- Add policy target input format.
- Add `profile_init` job.

### Initial Profile Fields

- `expected_annual_return`
- `max_tolerable_drawdown`
- `investment_horizon_years`
- `monthly_contribution`
- `liquidity_buffer_months`
- `allowed_asset_types`
- `disallowed_asset_types`
- `max_single_position_weight`
- `max_sector_weight`
- `max_industry_weight`
- `max_theme_weight`
- `max_country_weight`
- `max_currency_weight`
- `max_monthly_turnover`
- `rebalance_band`
- `trade_mode`

### Acceptance Criteria

- A default advanced investor profile can be seeded.
- Invalid profiles block portfolio action generation.
- Unrealistic return/drawdown combinations produce warnings.
- Policy targets can be stored and read.
- `bounded_auto` is rejected in MVP.

## Sprint 004: Portfolio Snapshot and Exposure

### Goal

Represent current holdings and compute portfolio exposure.

```text
Holdings Input
  -> Current Weights
  -> Exposure by position / sector / industry / theme / country / currency
  -> Drift from Policy
```

### Scope

- Add `portfolios` table.
- Add `portfolio_holdings` table.
- Add manual CSV or YAML holdings import.
- Compute market value and weights from latest prices.
- Compute policy drift.
- Compute exposure by:
  - asset;
  - sector;
  - industry;
  - theme;
  - country;
  - currency.
- Add `portfolio_snapshot` job.

### Acceptance Criteria

- Holdings can be imported without broker integration.
- Portfolio weights sum to approximately 1.0.
- Concentration checks compare current weights to profile limits.
- Drift checks compare current sleeve weights to policy targets.
- Missing price for one holding logs the issue and skips or marks that holding without crashing the full snapshot.

## Sprint 005: Screening and Sector/Theme Analysis

### Goal

Turn asset-level factors into candidate rankings and portfolio-aware sector/theme inputs.

```text
Factor Values
  -> Normalization
  -> Candidate Ranking
  -> Sector / Theme Aggregation
```

### Scope

- Implement `screening/run_screening.py`.
- Normalize factors by percentile within universe.
- Apply MacroState-adjusted screening params.
- Store ranked candidates in `screening_results`.
- Add initial sector and theme tag support in `assets.metadata`.
- Compute sector/theme scores from asset-level factors.
- Block or de-prioritize candidates that would worsen profile constraint violations.

### Acceptance Criteria

- Screening produces ranked candidates from `factor_values`.
- Screening results are stored with `run_id`, `score`, `rank`, and `reason`.
- MacroState adjusts weights and candidate count but does not bypass profile constraints.
- Overexposed sectors/themes are flagged before new buys are proposed.

## Sprint 006: Rebalancing Proposal Engine

### Goal

Deliver Level 1 MVP: deterministic rebalancing proposals.

```text
Profile + Policy + Holdings + MacroState + Screening
  -> Rebalancing Rules
  -> Portfolio Action Report
```

### Scope

- Add `rebalance_runs` table.
- Add `proposed_actions` table.
- Implement action types:
  - `hold`
  - `trim`
  - `add`
  - `rebalance_to_band`
  - `watch`
  - `block_new_buy`
  - `raise_cash`
- Implement reason codes:
  - `POSITION_OVER_MAX`
  - `SECTOR_OVER_MAX`
  - `SLEEVE_OVER_BAND`
  - `SLEEVE_UNDER_BAND`
  - `CASH_BELOW_BUFFER`
  - `MACRO_CAUTIOUS_TIGHTEN_RISK`
  - `MACRO_DEFENSIVE_REDUCE_CONCENTRATION`
  - `FACTOR_SCORE_SUPPORTS_ADD`
  - `VALUATION_TOO_EXPENSIVE`
  - `QUALITATIVE_RESEARCH_REQUIRED`
  - `TURNOVER_LIMIT`
  - `NO_ACTION_WITHIN_POLICY`
- Add `rebalance_check` job.
- Generate Markdown and CSV portfolio action reports.

### Acceptance Criteria

- If the portfolio is within policy bands, Croesus produces a no-action report.
- If a position exceeds profile max, Croesus proposes a trim.
- If a sleeve is under target beyond rebalance band, Croesus proposes an add or rebalance action.
- If MacroState is `Cautious` or `Defensive`, new satellite adds are restricted.
- Proposed actions respect max turnover.
- No order is submitted.

## Sprint 007: Valuation Layer

### Goal

Add equity valuation as a deterministic input to screening and rebalancing.

```text
Fundamentals
  -> Relative Valuation
  -> DCF Snapshot
  -> factor_values
```

### Scope

- Add `fundamentals` table.
- Add `valuation_snapshots` table.
- Add `FundamentalsProvider`.
- Add yfinance fundamentals provider.
- Ingest financial statements.
- Compute:
  - `pe_ratio`
  - `pb_ratio`
  - `ev_to_ebitda`
  - `fcf_yield`
  - sector percentiles
  - `price_to_intrinsic`
- Add `quarterly_run`.

### Acceptance Criteria

- Valuation factors appear in `factor_values`.
- DCF details appear in `valuation_snapshots`.
- Assets with insufficient financial data are skipped with logs.
- Rebalancing can use valuation as a reason to avoid, watch, or size down a candidate.

## Sprint 008: Research Agent

### Goal

Use LLMs only for qualitative research on shortlisted candidates and proposed actions.

### Scope

- Define research input contract.
- Collect or load news, filings, earnings-call text, and company descriptions.
- Summarize:
  - business model;
  - recent developments;
  - competitive position;
  - management commentary;
  - regulatory risk;
  - key risks.
- Attach research summaries to proposed actions where `requires_research = true`.

### Acceptance Criteria

- Research Agent runs only after deterministic screening and portfolio filters.
- LLM output is stored separately from computed factors.
- LLM does not compute factor values, risk metrics, or constraint checks.
- Portfolio action reports can include qualitative risk summaries.

## Sprint 009: Approval-Based Execution

### Goal

Prepare broker orders only after explicit user approval.

### Scope

- Add execution plan data model.
- Convert proposed actions into draft orders.
- Add approval gate.
- Add dry-run broker adapter.
- Add audit log.

### Acceptance Criteria

- Draft orders are not created unless user explicitly approves a proposal.
- Dry-run mode produces an order preview.
- No real broker integration is required.
- Every execution plan is traceable to a rebalance run and approved proposal.

## Sprint 010: Bounded Automation

### Goal

Allow long-horizon automated rebalancing under strict guardrails.

### Scope

- Enable `bounded_auto` trade mode.
- Add kill switch.
- Add stale-data checks.
- Add max trade value per day.
- Add max turnover per month enforcement.
- Add broker adapter only after dry-run execution is proven.

### Acceptance Criteria

- Bounded automation refuses to run with stale prices or invalid profile.
- Actions that exceed guardrails require manual approval.
- All trades are logged with input data, reason codes, and profile constraints.
- No high-frequency or intraday trading behavior is introduced.

## MVP Definition

MVP Level 1 is complete when Sprints 001 through 006 are implemented.

The system should be able to:

1. Load an advanced investor profile.
2. Load policy portfolio targets.
3. Load current holdings.
4. Compute market, factor, macro, and exposure inputs.
5. Determine whether rebalancing is needed.
6. Produce a clear portfolio action report.
7. Submit no trades.

## Relationship to Existing Planning Docs

- `docs/planning/sprint-001-asset-registry.md` remains valid for data foundation.
- `docs/planning/sprint-002-macro-analysis.md` remains valid for MacroState.
- `docs/planning/sprint-007-valuation-analysis.md` contains the valuation implementation plan and is sequenced after profile and portfolio foundations for product coherence.
