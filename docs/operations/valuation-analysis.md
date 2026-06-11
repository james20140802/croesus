# Valuation Analysis Layer

Sprint 007 adds a fundamental-valuation view to the Factor Engine. Earlier
factors are all price-derived (momentum, volatility, liquidity, 200d MA); this
layer answers a different question:

> Is this stock cheap or expensive relative to its own fundamentals?

```text
Fundamentals Ingestion (yfinance)
  -> fundamentals table
  -> Relative Valuation  (P/E, P/B, EV/EBITDA, FCF yield + sector percentiles)
  -> Absolute Valuation  (2-stage DCF with CAPM WACC)
  -> factor_values       (screening consumes these unchanged)
  -> valuation_snapshots (DCF detail + assumptions)
```

It is **computation and recording only** — it never trades. It is equity-only,
US-only, and single-currency (USD) this sprint.

## Two cadences

Multiples move with today's price, so they refresh **daily**; the DCF depends on
quarterly financial statements, so it refreshes **quarterly**.

```bash
python -m croesus.jobs.daily_run        # multiples + sector percentiles (no DCF)
python -m croesus.jobs.quarterly_run    # ingest fundamentals + recompute DCF
```

Both share one entry point, `compute_and_store_valuation_factors(conn,
include_dcf=...)`, so the two cadences cannot drift apart.

## The eight factors (in `factor_values`)

| factor_name | meaning |
|---|---|
| `pe_ratio` | price / latest annual EPS |
| `pb_ratio` | price / book value per share |
| `ev_to_ebitda` | (market cap + total debt − cash) / EBITDA |
| `fcf_yield` | free cash flow / market cap |
| `pe_vs_sector_pct` | P/E percentile within the asset's sector (0 = cheapest) |
| `pb_vs_sector_pct` | P/B percentile within sector |
| `ev_ebitda_vs_sector_pct` | EV/EBITDA percentile within sector |
| `price_to_intrinsic` | price / DCF intrinsic value (quarterly) |

Sector percentiles are a 0–100 ascending mid-rank: **0 = most undervalued**.

## DCF (in `valuation_snapshots`)

Two-stage: a 5-year explicit FCF projection plus a Gordon-growth terminal value,
discounted at an all-equity CAPM cost of capital.

- **WACC** = `Rf + β × 5.5%`. `Rf` is the latest macro 10Y Treasury
  (`macro_scores.raw_indicators["DGS10"]`, a percent), defaulting to **4.5%**
  when macro data is absent.
- **β** = 2-year daily-return regression vs **SPY**, falling back to the
  **sector median**, then **1.0**. SPY is seeded as an ETF benchmark
  (`seed_benchmarks`) and priced by `daily_run`, so each name gets a real beta
  once prices exist (e.g. NVDA ≈ 2.0, MSFT ≈ 0.9); the fallbacks only apply
  before SPY has enough price history. SPY is an ETF, so it is never itself a
  valuation target — the equity-only loops skip it.
- **FCF growth** = 5-year CAGR, clipped to `[-5%, +30%]`.
- **Terminal growth** = 2.5%.

The row stores intrinsic value, current price, upside %, WACC, both growth rates,
and an `assumptions_json` blob recording every input (`"source": "model"`).

### LLM override extension point

`two_stage_dcf` and the orchestration are designed so a future LLM can override
assumptions; the snapshot would then record `"source": "llm_override"`. The
interface exists this sprint; the override path is not yet wired.

## Honest empties (data-deficiency rules)

| situation | handling |
|---|---|
| fewer than 3 annual FCF years | DCF skipped, `price_to_intrinsic` left NULL |
| FCF negative across all periods | DCF skipped, logged |
| FCF growth not estimable (sign change) | DCF skipped, logged |
| WACC ≤ terminal growth (divergence) | DCF skipped, logged |
| multiple denominator 0 / NULL / negative | that factor left NULL |
| no price on/before the as-of date | asset skipped |

Per-asset failures are always logged and skipped — one bad symbol never stops
the run.

## `fundamentals` table

Long format like `factor_values`: one row per
`(asset_id, period_end, period_type, metric_name)`. `metric_name` values are a
stable contract: `revenue`, `operating_income`, `net_income`, `eps`,
`free_cash_flow`, `total_debt`, `total_equity`, `cash_and_equivalents`,
`shares_outstanding`, `ebitda`, `capex`, `book_value_per_share`
(`book_value_per_share` is derived = total_equity / shares_outstanding).

yfinance line-item labels are mapped onto this vocabulary by an explicit
priority map in `ingest_fundamentals.py`; unmapped labels are ignored and absent
metrics are simply not stored (NULL by omission).

## Manual verification

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("""
        SELECT asset_id, period_end, metric_name, value FROM fundamentals
        WHERE metric_name IN ('free_cash_flow', 'eps')
        ORDER BY asset_id, period_end DESC
    """).df())
    print(conn.execute("""
        SELECT asset_id, date, intrinsic_value_per_share, current_price, upside_pct, wacc
        FROM valuation_snapshots ORDER BY date DESC
    """).df())
    print(conn.execute("""
        SELECT asset_id, factor_name, value FROM factor_values
        WHERE factor_name IN ('pe_ratio', 'pb_ratio', 'pe_vs_sector_pct', 'price_to_intrinsic')
        ORDER BY asset_id
    """).df())
```

## Out of scope

Debt-weighted WACC, TTM EPS, LLM assumption overrides (interface only),
quality/growth/leverage factors, non-equity valuation, paid data sources, and
DCF scenario analysis (bull/base/bear).
