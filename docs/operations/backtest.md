# Backtest Harness — Operations Guide

Sprint 014 implemented a walk-forward backtest of the screening + rebalancing
rules so you can answer: **"did this screening rule actually work historically?"**

## How to Run

```bash
python -m croesus.jobs.backtest_run \
    --start 2020-01-01 \
    --end   2024-12-31 \
    --top-n 5 \
    --cost-bps 10 \
    --reports-dir reports \
    --db-path storage/croesus.duckdb
```

All arguments except `--start` and `--end` have defaults. The job:

1. Loads close prices for every active equity/ETF asset in the DB.
2. Determines monthly rebalance dates (first trading day of each calendar month).
3. For each rebalance date, recomputes price factors **point-in-time** (only
   data up to that date) using `croesus/factors/common.py`.
4. Percentile-ranks each factor across the live universe on that date.
5. Computes the weighted composite score for each scheme in `BacktestConfig`.
6. Selects the top-N assets, equal-weights them, applies round-trip costs.
7. Tracks portfolio value daily from close-to-close returns.
8. Writes output to `reports/backtest/<end-date>/backtest.md` and `.csv`.

The terminal also prints the comparison table directly.

## How to Read the Output Table

```
Scheme            CAGR     Sharpe    MDD      TotRet   Turnover
----------------------------------------------------------------------
composite_v1      12.34%   0.823   -18.50%   74.21%    3.142
momentum_only     15.01%   0.912   -22.10%   92.34%    4.205
SPY (B&H)          9.87%   0.651   -19.41%   58.66%    0.000
```

| Column | Meaning |
|---|---|
| CAGR | Compound Annual Growth Rate over the full window |
| Sharpe | Annualised Sharpe ratio (daily returns, √252, zero risk-free rate) |
| MDD | Maximum peak-to-trough drawdown (negative number; closer to 0 is better) |
| TotRet | Simple total return from first to last day |
| Turnover | Sum of `|w_new - w_old|/2` across all rebalances (1.0 = full portfolio replaced once) |

The **benchmark** row (`SPY (B&H)`) is a buy-and-hold position in the
benchmark symbol started on the first day of the backtest window.

## Weight Schemes and the Mix-vs-Integrate Question

`BacktestConfig.weight_schemes` is a dict mapping a scheme name to a set of
dimension weights. The two built-in schemes are:

- **`composite_v1`** — the live system's default weights (momentum 0.35,
  liquidity 0.25, trend 0.25, volatility penalty 0.15). This is the
  "integrated composite" approach from the literature review.

- **`momentum_only`** — momentum weight 1.0, all other dimensions zero. This
  is the simplest possible factor model and serves as a "how much does the
  multi-factor mix actually help?" baseline.

You can add custom schemes by passing a `weight_schemes` dict to
`BacktestConfig` in code, or by modifying `_default_schemes()` in
`croesus/backtest/config.py`.

**Interpreting the A/B result**: if `composite_v1` beats `momentum_only` on
Sharpe and MDD with similar CAGR, the multi-factor mix is adding risk-adjusted
value. If `momentum_only` dominates on all metrics, the extra dimensions may be
adding noise rather than signal in this dataset.

This answers the "mix-vs-integrate" question from the Sprint 008 roadmap —
not by assertion, but by empirical replay on the actual DB data.

## Four Honest Limitations

These limitations are always printed in the Markdown report. They are
reproduced here for reference.

1. **Price factors are clean point-in-time.** Momentum, volatility, liquidity,
   and the 200-day moving average are recomputed from historical closes using
   only data available up to each rebalance date. There is no look-ahead bias
   in the price factors.

2. **Valuation factors are EXCLUDED from this backtest entirely.** yfinance
   provides only the *latest* financial statements (P/E, P/B, EV/EBITDA, FCF
   yield, price-to-intrinsic). Reconstructing historical valuation scores would
   require point-in-time fundamental data (e.g. Compustat), which is not
   available in this system. Using current statements as a proxy for past ones
   would introduce severe look-ahead bias. Therefore valuation is entirely
   absent from backtest scoring; live screening may behave differently.

3. **Survivorship bias.** The asset universe is today's active set in the
   `assets` table. Companies that were delisted, acquired, or removed from an
   index during the backtest window are not present. This systematically
   inflates returns because the historical losers that no longer exist are
   excluded from the selection pool.

4. **Macro-regime weight tilts are not replayed.** In the live system, the
   Macro Analysis Layer dynamically adjusts dimension weights based on the
   current growth/inflation regime. This backtest uses *static* weights as
   defined in each scheme. The A/B comparison tests which static weight
   allocation was historically better, not whether the dynamic tilt added
   value — that is a separate and harder analysis.
