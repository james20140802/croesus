# Normalized Reverse-DCF Opportunity Methodology

Methodology C: a *reverse-DCF on a normalized FCF base*, separate from the
mechanical two-stage DCF (Methodology A). Both live in the opportunity
registry and are forward-tested side-by-side; neither replaces the other.

## Why a second methodology?

The mechanical DCF uses the **latest annual FCF** as its base and a **5-year
CAGR** as growth. Both are vulnerable to endpoint artifacts: a flat compounder
whose FCF happened to peak three years ago and trough last year looks like a
–4%/yr decliner even though the underlying trend is flat.

This methodology addresses two separate questions the mechanical model cannot:

1. **Normalization problem:** is the latest FCF year representative, or is it a
   one-year artifact?
2. **Plausibility problem:** given what the market is pricing in, does the
   implied FCF growth rate make sense compared to the company's own FCF
   history?

### Worked example — AAPL, observed 2026-06-30

AAPL annual FCF in the DB:

| Year | FCF (B USD) |
|------|------------|
| 2022 | 111.4      |
| 2023 | 99.6       |
| 2024 | 108.8      |
| 2025 | 98.8 (trough) |

- **Mechanical CAGR** `(98.8 / 111.4)^(1/3) − 1 = −3.9%`. That is an endpoint
  artifact; the series is approximately flat.
- **Median (normalized base)** = `(99.6 + 108.8) / 2 = 104.2 B`.
- **Log-linear slope** (OLS on ln(FCF) vs year index) = **−2.7%/yr** — still
  slightly negative, but far less distorted than the CAGR.
- **Reverse DCF** at price $281.74, moat-adjusted knobs (10-year CAP,
  terminal 3.0%, WACC 11.49%): the market is implying **+20.0%/yr** FCF
  growth for 10 years.
- **`plausibility_gap`** = `20.0% − (−2.7%) = 22.7 points`. "The market
  needs 22.7 percentage points more annual growth than the FCF trend
  supports." Checkable. Falsifiable.
- The **normalized intrinsic floor** (using median base at −2.7% growth)
  rises from $46.24 (mechanical) to $53.64 (log-lin) or $65.27 (flat) —
  the floor is honestly higher once the trough endpoint is removed, without
  touching the mechanical model.

## Math

### Normalized base FCF

```
normalized_base_fcf = median(annual_fcf[-window:])
```

`window = 10` years by default. Median damps a single peak or trough year that
would otherwise dominate CAGR.

### Reference growth (log-linear slope)

```
reference_growth = exp(OLS_slope(ln(FCF_i), i)) - 1
```

Only positive FCF points are included; their original year indices are
preserved so gaps from non-positive years do not collapse. Requires at least
2 positive FCF points; clipped to the same `[FCF_GROWTH_FLOOR, FCF_GROWTH_CAP]`
band as the mechanical model.

### Normalized intrinsic value

```
normalized_intrinsic = two_stage_dcf(
    base_fcf=normalized_base_fcf,
    growth_rate=reference_growth,
    wacc=wacc,                    # reused from mechanical run
    ...moat-adjusted knobs...
).intrinsic_value_per_share
```

The same `two_stage_dcf` function the mechanical model uses; only the inputs
change.

### Reverse-DCF implied growth

Bisection on `g ∈ [−50%, +100%]`:

```
Find g such that:
  two_stage_dcf(base_fcf=normalized_base_fcf, growth_rate=g, ...).intrinsic == price
```

`None` when the price is outside the bracket (implies growth > 100%/yr or
< −50%/yr).

### Plausibility gap

```
plausibility_gap = implied_growth − reference_growth
```

**Negative = the market prices in less growth than the FCF trend → cheap.**
**Positive = the market prices in more growth than the trend → expensive.**

Ranking is ascending by `plausibility_gap`; `None` sorts last.

## `valuation_quality` flags

| flag | meaning |
|------|---------|
| `ok` | normalized base FCF positive, reference growth defined and not clip-pinned, ≥ 4 FCF years |
| `reference_unreliable` | reference growth saturated at a clip boundary (≤ −5% floor or ≥ +30% cap); the gap anchor is pinned, so the plausibility gap is not trustworthy. Computed and persisted but ranked **below** `ok` names (deprioritized, not skipped) |
| `short_history` | computed but fewer than 4 FCF years available; estimates are less stable |
| `fcf_not_meaningful` | base FCF ≤ 0 or reference growth undefined (sign-flipping FCF, e.g. JPM); methodology skipped for this asset |

**Financials are excluded by sector** in the orchestration layer (`Financials` /
`Financial Services`), not just by the FCF-sign check. Banks, insurers, and
brokers report accounting FCF with no meaningful capex/working-capital structure
(JPM FCF: 107 → 13 → −42 → −148 B; insurers like PGR/ALL/CBOE show large but
misleading *positive* FCF). Without this exclusion they dominated the cheap end
of the gap ranking with absurd per-share intrinsics. Excluded names are skipped
with reason `financial sector (FCF-DCF n/a)`.

**Why deprioritize, not drop, `reference_unreliable`?** Roughly 1/3 of names pin
the +30% growth cap on a fast recent FCF run; their gaps are huge-negative
artifacts of the saturated anchor, not real cheapness. Ranking by a quality tier
first (`ok` above flagged) keeps the cheap end trustworthy while still persisting
the flagged breakdown for inspection.

## WACC reuse contract

The normalized methodology does **not** recompute beta or WACC. It reads the
WACC already persisted in `valuation_snapshots` by the mechanical run. The
quarterly pipeline runs the mechanical DCF first, then the normalized DCF
second; an asset with no mechanical snapshot for the `as_of` date is skipped
with reason `"no mechanical wacc"`. This keeps the two methodologies on
identical discount-rate assumptions and eliminates any beta-computation
difference as a confounding variable.

## Coexistence with Methodology A (mechanical DCF)

| dimension | Methodology A (`moat_adjusted_intrinsic_value`) | Methodology C (`normalized_dcf`) |
|---|---|---|
| FCF base | latest annual FCF | median of ≤ 10 years |
| growth input | 5-year CAGR | log-linear OLS slope |
| CAP period | 5 years | 10 years (moat-adjusted) |
| terminal growth | 2.5% | 3.0% (moat-adjusted) |
| output | `valuation_snapshots` | `normalized_dcf_snapshots` |
| ranking signal | upside % | `plausibility_gap` (ascending) |
| table modified | `valuation_snapshots`, `factor_values` | `normalized_dcf_snapshots` only |

**Methodology A is not modified by this plan.** The new methodology is
additive: a separate registry entry, a separate DB table, a separate review
function. Both are selectable from the opportunity review CLI and are tracked
in the forward test out-of-sample against SPY.

## Data flow

```
quarterly_run
  -> compute_and_store_valuation_factors (Methodology A)  [first]
  -> compute_and_store_normalized_dcf    (Methodology C)  [second, reuses WACC]
     -> reads:  fundamentals.free_cash_flow (≤10 years)
                valuation_snapshots.wacc
                prices_daily.close
     -> writes: normalized_dcf_snapshots
```

## Persistence (`normalized_dcf_snapshots`)

One row per `(asset_id, date)` primary key. Columns include `normalized_base_fcf`,
`reference_growth`, `normalized_intrinsic_value_per_share`, `normalized_upside_pct`,
`implied_growth`, `plausibility_gap`, `valuation_quality`, `n_fcf_years`, `wacc`,
and an `assumptions_json` blob. Upserted each quarterly run.

## Report output

For `normalized_dcf` cards the opportunity report shows:

```
implied growth 20.0% vs FCF trend -2.7%  ->  plausibility gap 22.7 pts  [ok]
normalized FCF floor upside -81.0%  (conservative floor, not fair value)
```

The "floor upside" line uses the normalized intrinsic (base + reference growth);
it is labeled as a **conservative floor** to distinguish it from the moat-adjusted
fair value that Methodology A targets.

## Manual verification

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("""
        SELECT asset_id, date, normalized_base_fcf, reference_growth,
               implied_growth, plausibility_gap, valuation_quality, n_fcf_years
        FROM normalized_dcf_snapshots
        ORDER BY date DESC, plausibility_gap ASC NULLS LAST
    """).df())
```

## Out of scope (this plan)

- Thesis-driven reference growth (moat/sector grades into growth floor).
- Composite multi-factor ranking blending this signal with Methodology A.
- BRK-B share-class correction.
- Promotion workflow (state machine: `review_only → watch → approved_for_action_review`).
- Web UI rendering of normalized-methodology detail cards.
- Multi-source FCF backfill (yfinance returns only ~4 years for many names;
  `short_history` flags the limitation until a dedicated backfill is built).
