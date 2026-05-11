# Investment Philosophy

## Core Philosophy

Croesus should support evidence-based, risk-aware, long-term investing.

The system should not attempt to replace human judgment. Instead, it should reduce the cost of repeated research, enforce explicit investment rules, and help the user avoid impulsive or poorly grounded decisions.

## Operating Principles

### 1. Deterministic computation first

If a signal can be calculated reliably with code, it should be calculated with code.

Examples:

- Returns.
- Volatility.
- Drawdown.
- Liquidity.
- Valuation ratios.
- Balance-sheet metrics.
- Portfolio weights.
- Risk exposure.

LLMs should not be responsible for basic calculations or constraint checks.

### 2. LLMs for unstructured information

LLMs are useful for interpreting information that is difficult to normalize directly:

- News.
- Earnings call transcripts.
- SEC filings.
- Management commentary.
- Competitive positioning.
- Regulatory risk.
- Product and industry narratives.

The system should clearly separate computed signals from LLM-generated interpretation.

### 3. Broad screening before deep research

Croesus should not deeply analyze every asset with expensive LLM workflows.

The intended funnel is:

1. Start from a broad asset universe.
2. Apply deterministic filters and factors.
3. Select a smaller candidate set.
4. Run deeper qualitative research only on the candidates.
5. Compare candidates with portfolio constraints.
6. Produce a recommendation or watchlist for user review.

### 4. Risk-aware portfolio construction

A high-quality asset is not always a good portfolio addition.

Croesus should consider:

- Position concentration.
- Sector concentration.
- Geographic exposure.
- Currency exposure.
- Liquidity.
- Volatility.
- Correlation with existing holdings.
- Time horizon.
- User risk tolerance.

### 5. User approval before execution

Croesus may eventually integrate with brokers or trading APIs, but trade execution should require explicit user approval.

The default system behavior should be:

- Analyze.
- Explain.
- Propose.
- Ask for confirmation.
- Then execute only if authorized.

## Initial Portfolio Principles

The initial portfolio should be simple, diversified, and explainable. It should avoid overfitting to short-term signals.

A reasonable initial approach is:

- Use broad-market exposure as a baseline.
- Add a limited number of high-conviction satellite positions.
- Avoid excessive concentration in a single company, sector, or theme.
- Prefer assets with sufficient liquidity and transparent data.
- Rebalance on a schedule or when risk constraints are violated.

## What Croesus Should Avoid

- Meme-stock style recommendations without fundamental support.
- Pure LLM-driven stock picking.
- Overly frequent trading without cost/risk analysis.
- Hidden assumptions in portfolio construction.
- Recommendations that cannot be traced back to data or explicit reasoning.
- Treating all asset classes as if they were common stocks.

## Decision Standard

Every recommendation should be explainable in this form:

> Croesus surfaced this asset because it passed these filters, ranked highly on these factors, has these qualitative considerations, and fits or conflicts with the current portfolio in these specific ways.
