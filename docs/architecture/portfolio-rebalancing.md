# Portfolio Rebalancing

## Purpose

The rebalancing engine turns analysis into portfolio action proposals.

It should answer:

> Given the investor profile, policy portfolio, current holdings, market regime, and asset analysis, should the portfolio change now?

Level 1 MVP ends at a proposal. It does not execute trades.

## Core Workflow

```text
Investor Profile
  -> Policy Portfolio
  -> Current Holdings Snapshot
  -> Portfolio Exposure Analysis
  -> Macro Risk Adjustment
  -> Candidate Asset Analysis
  -> Rebalancing Decision
  -> Portfolio Action Report
```

## Inputs

### Investor Profile

Defines risk and operating constraints:

- Expected return.
- Maximum drawdown.
- Concentration limits.
- Allowed asset classes.
- Rebalance band.
- Turnover limit.
- Trade mode.

See `docs/architecture/investor-profile.md`.

### Policy Portfolio

Defines target sleeves and acceptable ranges. A sleeve may be broad market exposure, cash, bonds, sector exposure, or satellite equities.

Example:

```text
Core US Equity: 55% target, 45-65% allowed
Satellite Equity: 15% target, 0-20% allowed
Defensive / Bonds: 20% target, 10-30% allowed
Cash: 10% target, 5-20% allowed
```

### Current Holdings

Stored holdings should include:

```text
portfolio_id
asset_id
quantity
market_value
currency
cost_basis
as_of_date
source
```

Level 1 can use manual CSV input. Brokerage synchronization is out of scope.

### MacroState

MacroState adjusts risk posture. It does not choose trades directly.

Suggested mapping:

| Macro positioning | Portfolio effect |
|---|---|
| Aggressive | allow upper range of satellite sleeve; loosen candidate count |
| Moderately Aggressive | allow normal risk budget |
| Neutral | keep policy targets unchanged |
| Cautious | reduce new satellite additions; tighten liquidity and quality filters |
| Defensive | prioritize concentration reduction and cash/defensive sleeve restoration |

The investor profile remains the outer constraint.

### Asset, Sector, and Company Analysis

The rebalancing engine consumes:

- Factor scores.
- Valuation metrics.
- Quality and growth metrics when available.
- Sector and theme exposure.
- LLM qualitative research summaries for shortlisted candidates.

## Exposure Analysis

Croesus should compute portfolio exposure before proposing any action:

```text
position_weight = holding_market_value / portfolio_market_value
sector_weight = sum(position weights by sector)
industry_weight = sum(position weights by industry)
theme_weight = sum(position weights by theme tag)
country_weight = sum(position weights by country)
currency_weight = sum(position weights by currency)
cash_weight = cash / portfolio_market_value
```

Initial exposure checks:

| Check | Example action |
|---|---|
| Single position exceeds profile max | propose trim |
| Sector exceeds profile max | block new buys in that sector; propose trim if severe |
| Sleeve is outside policy band | propose rebalance toward band |
| Cash below liquidity buffer | propose raise cash or reduce new buys |
| Monthly turnover limit would be exceeded | reduce proposal size |

## Rebalancing Decision Model

The MVP should use deterministic rules, not LLM judgment.

Decision order:

1. Validate investor profile.
2. Load current holdings and prices.
3. Compute current portfolio weights.
4. Compute drift from policy targets.
5. Compute exposure limit violations.
6. Load latest MacroState and derive risk posture.
7. Generate candidate actions:
   - trim overweight positions;
   - add to underweight policy sleeves;
   - hold when within policy;
   - watchlist candidates for future research.
8. Apply turnover and concentration constraints.
9. Generate a proposal with reasons.

## Action Types

Initial action types:

```text
hold
trim
add
rebalance_to_band
watch
block_new_buy
raise_cash
```

Each proposed action should include:

```text
action_id
portfolio_id
asset_id or sleeve_name
action_type
current_weight
target_weight
proposed_weight
estimated_trade_value
reason_codes
human_readable_reason
requires_research
requires_user_approval
```

## Reason Codes

Use stable reason codes so reports and tests can assert behavior.

Examples:

```text
PROFILE_INVALID
POSITION_OVER_MAX
SECTOR_OVER_MAX
SLEEVE_OVER_BAND
SLEEVE_UNDER_BAND
CASH_BELOW_BUFFER
MACRO_CAUTIOUS_TIGHTEN_RISK
MACRO_DEFENSIVE_REDUCE_CONCENTRATION
FACTOR_SCORE_SUPPORTS_ADD
VALUATION_TOO_EXPENSIVE
QUALITATIVE_RESEARCH_REQUIRED
TURNOVER_LIMIT
NO_ACTION_WITHIN_POLICY
```

## Output Report

The Level 1 report should be direct and action-oriented:

```text
# Portfolio Action Report

## Summary
- Profile: Growth-oriented long-term taxable account
- Portfolio value: 100,000 USD
- Macro posture: Cautious
- Decision: Rebalance recommended

## Current Issues
- NVDA is 14.2% of portfolio; profile max is 10.0%.
- Technology exposure is 41.0%; profile max is 35.0%.
- Cash is 3.5%; target minimum is 5.0%.

## Proposed Actions
1. Trim NVDA from 14.2% to 10.0%.
2. Move proceeds to broad market ETF and cash sleeve.
3. Block new semiconductor buys until theme exposure falls below limit.

## Why
- Position and sector concentration exceed profile limits.
- MacroState is Cautious, so new satellite exposure is restricted.
- Existing company quality remains high, so full exit is not recommended.
```

## Execution Levels

Croesus should separate proposal, approval, and execution.

```text
Level 0: Reports only
Level 1: Rebalancing proposals
Level 2: User-approved order generation
Level 3: Bounded automation with strict guardrails
```

Level 1 is the MVP target. Level 2 and Level 3 are future work.

## Safety Constraints

Even in future bounded automation, Croesus must not trade when:

- The investor profile is invalid.
- Price data is stale.
- Portfolio value cannot be reconciled.
- A proposed action violates concentration limits.
- Turnover exceeds the profile limit.
- The action requires qualitative research and no research summary exists.
- Trade mode does not allow execution.
- A kill switch is active.

## Out of Scope for Level 1

- Broker API integration.
- Order routing.
- Tax-aware lot selection.
- Intraday execution optimization.
- Margin, shorts, options, or leverage.
- Full portfolio optimization.
