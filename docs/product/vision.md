# Croesus Product Vision

## Summary

Croesus is an investment research agent that continuously collects market and company data, computes deterministic investment signals, and generates research reports to support disciplined portfolio decisions.

The initial goal is not autonomous trading. The first goal is to build a reliable research pipeline that can screen a broad asset universe and explain why certain assets deserve deeper review.

## Product Direction

Croesus should behave less like a chatbot that answers ad hoc stock questions and more like a lightweight research desk:

1. Maintain an asset universe.
2. Collect price, fundamental, filing, and news data.
3. Compute quantitative factors with deterministic code.
4. Screen and rank assets according to explicit investment rules.
5. Use LLMs for unstructured information such as news, filings, earnings calls, and narrative risk.
6. Generate daily or weekly research reports.
7. Compare candidate assets with the user's portfolio.
8. Require explicit user approval before any trade execution.

## Long-Term Expansion

Croesus should be designed to expand in stages:

1. Seed US equities.
2. S&P 500 / NASDAQ 100.
3. All US-listed equities.
4. Global equities.
5. ETFs and REITs.
6. Bond ETFs, commodities, FX, crypto, and other investment products.

This means the system should not hard-code assumptions that every asset is a US common stock.

## Core Principle

Calculable signals should be computed by code. LLMs should be used for interpretation, summarization, and qualitative synthesis, not for basic arithmetic, factor computation, or portfolio constraint enforcement.

## Initial MVP

The first MVP should answer:

> Among the currently supported US equities, which assets deserve closer research today, and why?

The MVP output should include:

- Asset registry entries.
- Daily price data.
- Common factor values such as momentum, volatility, and liquidity.
- Screening rankings.
- Markdown and CSV research outputs.

## Non-Goals for the Initial Version

- Fully autonomous trading.
- Real-time market data.
- Complex multi-agent orchestration.
- Full-scale backtesting engine.
- Web dashboard.
- Brokerage integration.
- Legal/compliance automation.

These may be explored later, but they should not block the first working research pipeline.
