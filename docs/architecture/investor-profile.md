# Investor Profile

## Purpose

The investor profile is the top-level mandate for Croesus. It defines what kind of portfolio the system is allowed to operate, what risks the investor accepts, and which actions should be proposed or blocked.

Croesus should not begin with "which stock looks attractive?" It should begin with:

> What portfolio is appropriate for this investor, and what constraints must every action obey?

## Core Idea

The profile is more specific than a simple risk label. Croesus should support direct input of return expectations, loss tolerance, investment horizon, exposure limits, and execution preferences.

The system should validate whether the profile is internally consistent. If an investor asks for high expected return with very low tolerable drawdown, Croesus should flag the mismatch and require adjustment before producing portfolio actions.

## Initial Profile Fields

```text
profile_id
name
base_currency
expected_annual_return
max_tolerable_drawdown
investment_horizon_years
monthly_contribution
liquidity_buffer_months
allowed_asset_types
disallowed_asset_types
max_single_position_weight
max_sector_weight
max_industry_weight
max_theme_weight
max_country_weight
max_currency_weight
max_monthly_turnover
rebalance_band
trade_mode
created_at
updated_at
metadata
```

Suggested `trade_mode` values:

```text
propose_only       -- generate reports and proposed actions only
approval_required  -- prepare orders but require explicit approval
bounded_auto       -- future mode; execute only within strict guardrails
```

Level 1 MVP should support `propose_only` only.

## Example Profile

```yaml
investor_profile:
  profile_id: default
  name: Growth-oriented long-term taxable account
  base_currency: USD
  expected_annual_return: 0.10
  max_tolerable_drawdown: -0.25
  investment_horizon_years: 10
  monthly_contribution: 2000
  liquidity_buffer_months: 6
  allowed_asset_types:
    - equity
    - etf
    - reit
    - cash
  disallowed_asset_types:
    - option
    - leveraged_etf
    - short_position
  max_single_position_weight: 0.10
  max_sector_weight: 0.35
  max_industry_weight: 0.25
  max_theme_weight: 0.30
  max_country_weight: 0.90
  max_currency_weight: 0.95
  max_monthly_turnover: 0.15
  rebalance_band: 0.05
  trade_mode: propose_only
```

## Profile Validation

Croesus should validate profile fields before portfolio analysis.

Initial validation rules:

| Rule | Behavior |
|---|---|
| `expected_annual_return <= 0` | invalid |
| `max_tolerable_drawdown >= 0` | invalid |
| `max_tolerable_drawdown > -0.05` and `expected_annual_return > 0.08` | warning: return/drawdown mismatch |
| `investment_horizon_years < 1` | invalid for long-term portfolio mode |
| `max_single_position_weight > max_sector_weight` | warning |
| `rebalance_band <= 0` | invalid |
| `max_monthly_turnover <= 0` | invalid |
| `trade_mode == bounded_auto` in MVP | invalid |

Warnings should not necessarily block report generation. Invalid profiles should block portfolio actions.

## Policy Portfolio

The policy portfolio is the target operating allocation derived from the investor profile.

In the MVP, this may be entered directly instead of inferred automatically:

```yaml
policy_portfolio:
  profile_id: default
  targets:
    US_EQ_BROAD: 0.55
    US_EQ_GROWTH: 0.15
    US_EQ_VALUE: 0.10
    US_BOND_AGG: 0.10
    CASH_USD: 0.10
```

Later, Croesus can suggest a policy portfolio from the profile, but Level 1 does not require a full optimizer.

## Relationship to Existing Engines

The investor profile is the outer constraint. Existing analysis modules feed into portfolio decisions, but none of them may bypass profile limits.

```text
Investor Profile
  -> Policy Portfolio
  -> Current Portfolio
  -> MacroState
  -> Factor / Sector / Company Analysis
  -> Rebalancing Proposal
```

Examples:

- A stock with a high factor score is rejected if it breaches max single-position weight.
- A sector with strong momentum is capped if the profile's max sector exposure is already reached.
- A defensive MacroState may reduce satellite exposure, but it cannot create leverage or short positions unless the profile allows them.

## Data Storage

Initial tables:

```sql
CREATE TABLE IF NOT EXISTS investor_profiles (
  profile_id TEXT PRIMARY KEY,
  name TEXT,
  base_currency TEXT,
  expected_annual_return DOUBLE,
  max_tolerable_drawdown DOUBLE,
  investment_horizon_years INTEGER,
  monthly_contribution DOUBLE,
  liquidity_buffer_months DOUBLE,
  allowed_asset_types JSON,
  disallowed_asset_types JSON,
  max_single_position_weight DOUBLE,
  max_sector_weight DOUBLE,
  max_industry_weight DOUBLE,
  max_theme_weight DOUBLE,
  max_country_weight DOUBLE,
  max_currency_weight DOUBLE,
  max_monthly_turnover DOUBLE,
  rebalance_band DOUBLE,
  trade_mode TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  metadata JSON
);
```

```sql
CREATE TABLE IF NOT EXISTS policy_targets (
  profile_id TEXT NOT NULL,
  sleeve_name TEXT NOT NULL,
  target_weight DOUBLE NOT NULL,
  min_weight DOUBLE,
  max_weight DOUBLE,
  metadata JSON,
  PRIMARY KEY (profile_id, sleeve_name)
);
```

## Out of Scope for Level 1

- Automated profile inference from questionnaires.
- Mean-variance optimization.
- Tax-aware asset location.
- Direct brokerage account synchronization.
- Automatic trade execution.
- Multi-user permissions.
