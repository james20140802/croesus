# Investment Philosophy

## Core Philosophy

Croesus should support evidence-based, risk-aware, long-term portfolio management for individual investors.

The system should not attempt to replace human judgment. Instead, it should reduce the cost of repeated research, enforce explicit investment rules, and help the user avoid impulsive or poorly grounded portfolio decisions.

The primary unit of judgment is not an isolated stock. It is the investor's portfolio under a specific investor profile.

## Operating Principles

### 1. Investor profile before asset selection

Croesus should first understand how the investor wants to operate:

- Expected annual return.
- Maximum tolerable drawdown.
- Investment horizon.
- Contribution pattern.
- Liquidity needs.
- Allowed and disallowed assets.
- Concentration limits.
- Rebalancing rules.
- Execution mode.

The profile defines the investment mandate. Every proposed action should be evaluated against this mandate before any asset-level attractiveness score matters.

### 2. Deterministic computation first

If a signal can be calculated reliably with code, it should be calculated with code.

Examples:

- Returns.
- Volatility.
- Drawdown.
- Liquidity.
- Valuation ratios.
- Balance-sheet metrics.
- Portfolio weights.
- Portfolio drift.
- Sector, industry, country, currency, and theme exposure.
- Risk exposure.
- Rebalancing thresholds.

LLMs should not be responsible for basic calculations, risk metrics, constraint checks, or trade authorization.

### 3. LLMs for unstructured information

LLMs are useful for interpreting information that is difficult to normalize directly:

- News.
- Earnings call transcripts.
- SEC filings.
- Management commentary.
- Competitive positioning.
- Regulatory risk.
- Product and industry narratives.

The system should clearly separate computed signals from LLM-generated interpretation.

### 4. Broad screening before deep research

Croesus should not deeply analyze every asset with expensive LLM workflows.

The intended funnel is:

1. Start from a supported asset universe.
2. Apply deterministic filters and factors.
3. Select a smaller candidate set.
4. Compare candidates with investor-profile and portfolio constraints.
5. Run deeper qualitative research only on candidates that remain viable.
6. Produce a watchlist, rebalance proposal, or no-action report for user review.

### 5. Risk-aware portfolio construction

A high-quality asset is not always a good portfolio addition.

Croesus should consider:

- Position concentration.
- Sector concentration.
- Industry and theme exposure.
- Geographic exposure.
- Currency exposure.
- Liquidity.
- Volatility.
- Correlation with existing holdings.
- Time horizon.
- User risk tolerance.
- Expected return versus drawdown consistency.

### 6. Macro as a risk-budget modifier

MacroState should not directly choose assets. It should adjust risk posture within the investor's profile.

Examples:

- In favorable conditions, allow a larger satellite sleeve or broader candidate set.
- In cautious conditions, tighten liquidity, quality, and volatility constraints.
- In defensive conditions, prefer reducing concentration and delaying new satellite exposure.

The investor profile remains the outer constraint. Macro analysis cannot override maximum drawdown, concentration, or liquidity limits.

### 7. User approval before execution

Croesus may eventually integrate with brokers or trading APIs, but trade execution should require explicit user approval until bounded automation is deliberately enabled.

The default system behavior should be:

- Analyze.
- Explain.
- Propose.
- Ask for confirmation.
- Then execute only if authorized.

Future bounded automation must still obey profile constraints, freshness checks, turnover limits, and a kill switch.

## Initial Portfolio Principles

The initial portfolio should be simple, diversified, and explainable. It should avoid overfitting to short-term signals.

A reasonable initial approach is:

- Use broad-market exposure as a baseline.
- Add a limited number of high-conviction satellite positions.
- Avoid excessive concentration in a single company, sector, industry, theme, country, or currency.
- Prefer assets with sufficient liquidity and transparent data.
- Rebalance on a schedule or when risk constraints are violated.
- Generate a no-action report when the portfolio remains within policy bands.

## What Croesus Should Avoid

- Meme-stock style recommendations without fundamental support.
- Pure LLM-driven stock picking.
- Treating a high factor score as an automatic buy signal.
- Overly frequent trading without cost, tax, and risk analysis.
- Hidden assumptions in portfolio construction.
- Recommendations that cannot be traced back to data or explicit reasoning.
- Treating all asset classes as if they were common stocks.
- Allowing MacroState to bypass investor-profile constraints.

## Decision Standard

Every recommendation should be explainable in this form:

> Croesus proposed this action because the current portfolio differs from the investor profile in these specific ways, the market regime changes the allowed risk posture in these ways, the affected assets have these quantitative and qualitative characteristics, and the proposed trade would move the portfolio closer to policy while respecting explicit constraints.
