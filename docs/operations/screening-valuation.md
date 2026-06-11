# Screening v2 — Valuation Dimension and Persisted Sub-Scores

Sprint 008b makes the Sprint 007 valuation factors actually drive decisions,
and stops discarding the per-dimension evidence behind the composite score.

## What changed

1. **Explicit factor registration** (`croesus/screening/dimensions.py`).
   The engine loads exactly the names registered there — ADR 0005's claim that
   `factor_values` rows integrate "automatically" was corrected. All 8
   valuation factors are now loaded.
2. **`valuation_score` dimension.** Universe percentiles of `pe_ratio`,
   `pb_ratio`, `ev_to_ebitda`, `price_to_intrinsic` are **inverted**
   (low multiple = cheap = good) and averaged with `fcf_yield`
   (already higher-is-better). The `*_vs_sector_pct` factors are exposed raw
   but excluded from the score — averaging both forms would double-count P/E.
3. **Missing fundamentals renormalize, never skip.** An asset with no
   valuation data keeps `valuation_score = null` and its `valuation` weight is
   redistributed across the other dimensions (same mechanism as the trend
   gate), so ETFs / new listings still rank on price factors.
4. **Persisted sub-scores.** `screening_results.factor_scores` now keeps the
   momentum horizon percentiles, the raw multiples, the gate state, and
   `valuation_score` — the inputs the report and the future Research Agent
   need to answer "why this name".
5. **Weights.** `valuation: 0.10` joins `base_weights`, redistributed out of
   trend (0.25 → 0.15) so the positive total stays 0.85. Regime tilts:
   Stagflation +0.10, Deflation +0.05, Goldilocks −0.05. Setting the
   valuation weight to 0 reproduces the pre-008b composite exactly.
6. **Expensive-add guard.** A `candidate` whose `price_to_intrinsic` exceeds
   1.25 becomes a watch action with `VALUATION_TOO_EXPENSIVE` (+
   `QUALITATIVE_RESEARCH_REQUIRED`) instead of a mechanical add — the reason
   code existed in the Sprint 006 spec but nothing generated it until now.
7. **Honest candidate counts.** `candidate_count` clamps to the ranked pool;
   `universe_size` / `ranked_count` / `effective_candidate_count` are exposed
   in `screening_params` so "top 20" can never silently mean "everything".

## Deliberately deferred (decided by data, not assertion)

`above_200d_ma` is a binary 0/1 percentile-ranked into `trend_score` — a known
design smell (the percentile of a binary variable is bimodal noise). It is left
**unchanged** this sprint so that v1/v2 scores stay comparable with the
valuation weight at 0; the gate-only-vs-score decision is queued for the
Sprint 014 backtest harness, which can A/B trend treatments alongside
mix-vs-integrate weight schemes.

## Reading the output

- Markdown report: `## Factor Breakdown` now has Valuation / P/E / P/B /
  Price-to-Intrinsic columns; `-` means no fundamentals (weight renormalized).
- CSV: adds `valuation_score`, the three `momentum_*_pct` columns, and the raw
  multiples.

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("""
        SELECT asset_id, score, factor_scores
        FROM screening_results
        WHERE run_id = (SELECT max(run_id) FROM screening_results)
        ORDER BY rank NULLS LAST LIMIT 10
    """).df())
```
