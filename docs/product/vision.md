# Croesus Product Vision

## Summary

Croesus is a personal portfolio management system. It helps an investor define an explicit investment profile, maintain a policy portfolio, analyze market and company data, and decide when portfolio rebalancing is warranted.

Croesus is not primarily a stock picker. Stock, sector, macro, and valuation analysis exist to answer a more important question:

> Given this investor's profile and current portfolio, what action should be taken now, if any?

The initial goal is not autonomous trading. The first product milestone is a reliable profile-based portfolio workflow that can diagnose a portfolio, surface candidate actions, and generate a clear rebalancing proposal.

## Product Direction

Croesus should behave less like a chatbot that answers ad hoc stock questions and more like a personal investment operating system:

1. Maintain an explicit investor profile.
2. Translate the profile into a policy portfolio and operating constraints.
3. Track the user's current holdings, cash, target weights, and drift.
4. Collect market, macro, price, fundamental, filing, and news data.
5. Compute quantitative signals with deterministic code.
6. Analyze sectors, themes, and individual companies as portfolio inputs.
7. Use LLMs for unstructured interpretation such as news, filings, earnings calls, and narrative risk.
8. Generate portfolio action reports and rebalancing proposals.
9. Require explicit user approval before any trade execution until bounded automation is deliberately enabled.

## Investor Profile First

The investor profile is the top-level input to Croesus. It should be advanced and explicit rather than a simple "conservative / moderate / aggressive" selector.

Initial profile fields should include:

- Expected annual return.
- Maximum tolerable drawdown.
- Investment horizon.
- Base currency.
- Monthly or periodic contribution.
- Cash or liquidity buffer.
- Allowed asset classes.
- Disallowed asset classes.
- Maximum single-position weight.
- Maximum sector, industry, theme, country, and currency exposure.
- Maximum monthly turnover.
- Rebalancing threshold or drift band.
- Trade mode: `propose_only`, `approval_required`, or future `bounded_auto`.

Croesus should validate the profile for internal consistency. For example, a high expected return with a very low maximum drawdown should be flagged as unrealistic rather than silently accepted.

## Portfolio Operating Model

Croesus should operate portfolios through this loop:

```text
Investor Profile
  -> Policy Portfolio
  -> Current Portfolio Snapshot
  -> Macro / Sector / Company Analysis
  -> Rebalancing Decision
  -> Portfolio Action Report
  -> User Approval
```

The existing research pipeline remains important, but it becomes a subordinate engine inside the portfolio workflow:

```text
Asset Universe
  -> Data Ingestion
  -> Factor Engine
  -> Screening Engine
  -> Research Agent
```

Screening results do not directly become trades. They become candidates that must pass investor-profile constraints, current-portfolio constraints, macro risk adjustment, and qualitative review.

## Long-Term Expansion

Croesus should expand in stages:

1. Seed US equities and simple portfolio holdings.
2. Profile-based policy portfolio and Level 1 rebalancing proposals.
3. S&P 500 / NASDAQ 100 universe coverage.
4. Sector and theme exposure analysis.
5. Equity valuation, quality, growth, and leverage factors.
6. Research Agent for candidate companies only.
7. ETFs and REITs.
8. Global equities.
9. Bond ETFs, commodities, FX, crypto, and other investment products.
10. Approval-based execution.
11. Bounded automation for long-horizon rebalancing, if explicitly enabled.

This means the system should not hard-code assumptions that every asset is a US common stock.

## Core Principle

Calculable signals should be computed by code. LLMs should be used for interpretation, summarization, and qualitative synthesis, not for basic arithmetic, factor computation, risk checks, portfolio constraints, or trade authorization.

## Initial MVP

The first portfolio MVP should answer:

> Given the investor profile and current holdings, does the portfolio need a rebalance today, and what actions should be proposed?

The MVP output should include:

- Investor profile record.
- Current holdings.
- Policy portfolio or target allocation.
- Asset registry entries.
- Daily price data.
- Common factor values such as momentum, volatility, and liquidity.
- MacroState and risk posture.
- Portfolio drift and concentration checks.
- Rebalancing proposal with clear reasons.
- Markdown and CSV portfolio action outputs.

Level 1 MVP scope ends at **rebalancing proposal**. It should not submit trades.

## Non-Goals for the Initial Version

- Fully autonomous trading.
- Real-time or high-frequency trading.
- Complex multi-agent orchestration.
- Full-scale backtesting engine.
- Web dashboard.
- Brokerage integration.
- Legal/compliance automation.
- LLM-generated factor values or portfolio constraint results.

These may be explored later, but they should not block the first working profile-based portfolio workflow.
