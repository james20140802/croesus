# Factor Engine

## Purpose

The factor engine computes deterministic investment signals from stored data.

It should be separate from data ingestion and separate from LLM-based research. Its output should be structured factor values that screening and portfolio modules can consume.

## Core Rule

If a signal can be computed with code, compute it with code.

The LLM layer may explain or contextualize factor values, but it should not be the source of truth for factor computation.

## Factor Categories

### Common factors

Common factors can apply to many asset types.

Initial common factors:

- Momentum.
- Volatility.
- Liquidity.
- Moving-average trend.

Examples:

```text
momentum_1m
momentum_3m
momentum_6m
volatility_3m
liquidity_1m
above_200d_ma
```

### Equity factors

Equity-specific factors are added after the common factor pipeline works (Sprint 003+).

**Sprint 003 — Valuation:**

Relative valuation (sector-percentile ranked) and absolute valuation (2-stage DCF). Requires `fundamentals` table populated by `quarterly_run`.

Scalar outputs to `factor_values`:

```text
pe_ratio              -- price / annual EPS
pb_ratio              -- price / book value per share
ev_to_ebitda          -- enterprise value / EBITDA
fcf_yield             -- free cash flow / market cap
pe_vs_sector_pct      -- PE percentile within sector (0 = cheapest)
pb_vs_sector_pct      -- PB percentile within sector
ev_ebitda_vs_sector_pct
price_to_intrinsic    -- current price / DCF intrinsic value (<1 = undervalued)
```

DCF detail output to `valuation_snapshots` (WACC, growth rate, intrinsic value per share, assumptions JSON).

See `docs/superpowers/specs/2026-05-28-valuation-analysis-design.md` for full methodology.

**Future sprints:**

- Quality: ROE, ROIC, gross margin, operating margin.
- Growth: revenue growth, EPS growth.
- Leverage: debt-to-equity, interest coverage.
- Capital efficiency: ROIC, asset turnover.

### ETF factors

ETF factors are different from stock factors.

Examples:

- Expense ratio.
- Assets under management.
- Tracking error.
- Holdings concentration.
- Sector exposure.
- Country exposure.
- Liquidity.

### Bond-related factors

Examples:

- Yield.
- Duration.
- Credit quality.
- Interest-rate sensitivity.
- Maturity profile.

### Crypto factors

Examples:

- Momentum.
- Volatility.
- Liquidity.
- Exchange coverage.
- On-chain activity.
- Narrative/news signal.

## Output Format

Computed factors should be written to `factor_values`.

```sql
CREATE TABLE IF NOT EXISTS factor_values (
  asset_id TEXT NOT NULL,
  date DATE NOT NULL,
  factor_name TEXT NOT NULL,
  value DOUBLE,
  PRIMARY KEY (asset_id, date, factor_name)
);
```

This long format is flexible because new factors can be added without changing table columns.

## Initial Common Factor Definitions

### momentum_1m

Approximate 21-trading-day return.

```text
close[today] / close[today - 21 trading days] - 1
```

### momentum_3m

Approximate 63-trading-day return.

```text
close[today] / close[today - 63 trading days] - 1
```

### momentum_6m

Approximate 126-trading-day return.

```text
close[today] / close[today - 126 trading days] - 1
```

### volatility_3m

Rolling standard deviation of daily returns over approximately 63 trading days.

### liquidity_1m

Average dollar volume over approximately 21 trading days.

```text
rolling_mean(close * volume, 21)
```

### above_200d_ma

Binary signal indicating whether the latest close is above the 200-day moving average.

## Normalization

Raw factor values are not directly comparable across factors.

The screening engine should normalize or rank factors before combining them.

Possible approaches:

- Percentile rank within universe.
- Z-score within universe.
- Winsorized z-score.
- Bucketed score.

For the MVP, percentile rank is sufficient.

## Scoring Example

A simple first scoring formula may be:

```text
total_score =
  0.35 * momentum_score
+ 0.25 * liquidity_score
+ 0.25 * trend_score
- 0.15 * volatility_penalty
```

This is not a final investment model. It is only a prototype ranking system for validating the pipeline.

## Implementation Layout

Recommended structure:

```text
factors/
  common.py
  compute_common_factors.py
  equity/
    valuation.py
    quality.py
    growth.py
  etf/
    exposure.py
    expense_ratio.py
  bond/
    duration.py
    credit.py
  crypto/
    liquidity.py
    onchain.py
```

## Sprint Scope

### Sprint 001 — Common factors

- `momentum_1m`
- `momentum_3m`
- `momentum_6m`
- `volatility_3m`
- `liquidity_1m`
- `above_200d_ma`

### Sprint 003 — Equity valuation factors

- `pe_ratio`, `pb_ratio`, `ev_to_ebitda`, `fcf_yield`
- `pe_vs_sector_pct`, `pb_vs_sector_pct`, `ev_ebitda_vs_sector_pct`
- `price_to_intrinsic` (DCF-based)

## Out of Scope (deferred)

- Full multi-factor equity model (quality, growth, leverage — future sprints).
- Portfolio optimization.
- Factor backtesting.
- Machine-learned alpha model.
- LLM-generated factor values.
- Debt-weighted WACC (Sprint 003 uses all-equity CAPM).
- TTM EPS from quarterly data (Sprint 003 uses annual EPS).

## Quality Requirements

The factor engine should:

- Skip assets with insufficient data.
- Log missing data clearly.
- Avoid crashing the whole run because one asset fails.
- Store factor values with a date.
- Keep factor names stable.
- Avoid source-specific assumptions where possible.
