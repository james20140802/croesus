# The Multi-Factor Model — Quality + Low-Beta Completion

This document describes the completed multi-factor scoring model: what each
factor is (input → process → output), how factors combine into a portfolio, the
candidate weight schemes, and the test results so far — with an honest reading of
what each number means and *why* it came out that way.

It is the strategy companion to:
- `docs/operations/backtest.md` — the historical walk-forward harness.
- `docs/operations/forward-test.md` — the out-of-sample track record.

---

## 1. Why a multi-factor model

A single signal is fragile. The institutional-alpha research (synthesized
separately) is blunt about what a no-leverage, long-only, public-data, large-cap
system can actually harvest: **not** speed, alt-data, or private markets — those
are structurally inaccessible — but **capacity-robust factor premia, combined**.
The single most defensible idea in that literature is the **value ⊕ momentum
negative correlation (≈ −0.6)**: both earn a positive long-run premium, and
because they zig when the other zags, a blend has a higher Sharpe than either
alone. Quality ("control your junk") and low-beta (Betting-Against-Beta, the
highest-Sharpe academic factor) round it out as defensive diversifiers.

So the model deliberately blends dimensions that are individually modest but
**mutually diversifying**. The completion adds the two that were missing:
**quality** and **low-beta**.

---

## 2. The factors (input → process → output)

Every factor follows the same shape: a raw per-asset number is computed
deterministically, cross-sectionally **percentile-ranked** across the universe
(so dimensions are comparable on a 0–1 scale), then blended into a **dimension
sub-score**, then into the **weighted composite**.

### 2.1 Momentum (`momentum_1m/3m/6m`) — existing
- **Input**: trailing daily closes.
- **Process**: price ratio over 21/63/126 trading days; percentile-ranked; the
  three horizons average into `momentum_score` (live screening weights them
  0.2/0.3/0.5 — 1m is a short-term reversal, downweighted).
- **Output**: `momentum_score` ∈ [0,1], higher = stronger recent winner.

### 2.2 Trend (`above_200d_ma`) — existing
- **Input**: close vs its 200-day moving average.
- **Process**: binary 1/0; under defensive macro postures it becomes an
  eligibility *gate* instead of a weighted score.
- **Output**: participation signal — is the name in an uptrend.

### 2.3 Liquidity (`liquidity_1m`) — existing
- **Input**: close × volume.
- **Process**: 21-day mean dollar volume; percentile-ranked.
- **Output**: `liquidity_score` — tradability / size proxy.

### 2.4 Volatility penalty (`volatility_3m`) — existing
- **Input**: daily returns.
- **Process**: 63-day standard deviation; percentile-ranked; **subtracted**.
- **Output**: penalizes total (idiosyncratic + systematic) risk.

### 2.5 Valuation (`valuation_score`) — existing (Sprint 007/008b)
- **Input**: P/E, P/B, EV/EBITDA, FCF yield, price-to-intrinsic (DCF).
- **Process**: each percentile-ranked; the multiples and price-to-intrinsic are
  **inverted** (low = cheap = good), FCF yield is natural; averaged.
- **Output**: `valuation_score` ∈ [0,1], higher = cheaper. Fundamental ⇒
  excluded from the backtest (look-ahead), live + forward-test only.

### 2.6 Low-beta (`beta_1y` → `low_beta` dimension) — **new**
- **Input**: the asset's daily returns and the market's (SPY) daily returns.
- **Process**: trailing-252-day OLS slope `beta = cov(r_asset, r_mkt) / var(r_mkt)`
  (reusing the existing `compute_beta` from the DCF/WACC code, aligned by date so
  it stays point-in-time in the backtest). Percentile-ranked, then **inverted**:
  `low_beta_score = 1 − percentile(beta)`.
- **Output**: `low_beta_score` ∈ [0,1], higher = lower systematic risk. Distinct
  from the volatility penalty: volatility is *total* risk; beta is *co-movement
  with the market*. The BAB literature ranks low-beta as the highest-Sharpe
  defensive factor. **Price-derived ⇒ backtestable** (unlike valuation/quality).

### 2.7 Quality (`roe`, `net_margin`, `debt_to_equity` → `quality_score`) — **new**
- **Input**: cached fundamentals — net income, revenue, total equity, total debt.
- **Process**: derive three primitives —
  - `roe` = net_income / total_equity (profitability on equity),
  - `net_margin` = net_income / revenue (profitability on sales),
  - `debt_to_equity` = total_debt / total_equity (leverage).

  Negative-equity names are skipped (ROE/leverage become meaningless). Each is
  percentile-ranked; ROE and margin are natural (high = good), leverage is
  **inverted** (low = good); averaged into `quality_score`.
- **Output**: `quality_score` ∈ [0,1], higher = more profitable + less levered.
  Fundamental ⇒ live + forward-test only (look-ahead in a backtest).

### 2.8 Missing-data rule (all dimensions)
A dimension with no data for an asset is **dropped and the remaining weights
renormalize** — a name without fundamentals (or without enough price history for
beta) is never penalized to zero; its weight is redistributed across the
dimensions it does have. This is why a no-fundamentals ETF still ranks on its
price factors alone.

---

## 3. From factor to portfolio (the pipeline)

```
factor_values (per asset, per date)
  → percentile_rank across the universe        (comparable 0–1 dimensions)
  → weighted composite score                   (scheme weights; vol & leverage subtracted/inverted)
  → rank, take top-N                           (with churn hysteresis: keep incumbents in a rank band)
  → redundancy-group-capped equal weight       (GOOG+GOOGL never take two slots)
  → cohort / holdings
```

The same scoring core serves three consumers: **live screening** (all dimensions,
macro-adjusted weights), the **backtest** (price dimensions only — momentum,
trend, liquidity, volatility, low-beta — valuation/quality excluded for
look-ahead), and the **forward-test** (all dimensions, recorded forward).

---

## 4. Schemes and their characters

| Scheme | Where | Weights | Character |
|---|---|---|---|
| `composite_v1` | backtest | mom .35 / liq .25 / trend .25 / vol −.15 | quality-momentum blue chips |
| `momentum_tilt` | backtest | mom .60 / trend .15 / vol −.25 | drawdown-aware momentum |
| `multifactor_lowbeta` | backtest | mom .45 / trend .15 / **low_beta .25** / vol −.15 | defensive momentum (BAB) |
| `momentum_only` | backtest | mom 1.0 | max raw return, max drawdown |
| `composite_live` | forward-test | live base (val .10) | baseline |
| `composite_v2_value` | forward-test | val .30 tilt | cheap financials/insurers |
| `composite_v3_multifactor` | forward-test | mom .25 / **val .25 / qual .20 / low_beta .15** / trend .15 | defensive value-quality |
| `momentum_aggressive` | forward-test | mom .85 / trend .15 | AI-hardware momentum (opt-in) |

**Live evidence the dimensions are real** — each scheme picks a *distinct*
roster, exactly as its weights predict (recorded 2026-06-12):

- `composite_v2_value` → STT, MET, BNY, PFG, CB, VZ — **cheap financials/insurers**.
- `momentum_aggressive` → MRVL, SNDK, MU, DELL, ARM, HPE — **AI-hardware momentum**.
- `composite_v3_multifactor` → MO, APA, ALL, EOG, GL, DVN, CB, CF, TRV, AIZ —
  **defensive value-quality**: cheap insurers (ALL P/E 5.8, TRV, CB) and energy
  (APA, EOG, DVN P/E 9–15) with **negative-to-low beta** (−0.85 to +0.39) and
  solid ROE (14–34%). The low-beta + value + quality blend converges on exactly
  the "cheap, profitable, market-insensitive" names the factors target — a
  portfolio that looks nothing like the cap-weighted index.

---

## 5. Test results

### 5.1 Backtest: does low-beta lower drawdown? (2021-01 → 2026-06, top-10, buffer 2.0)

| Scheme | CAGR | Sharpe | MDD | Total Return | Turnover |
|---|---|---|---|---|---|
| composite_v1 (mom/liq/trend/vol) | 13.13% | 0.81 | **-16.44%** | 94.82% | 33 |
| momentum_tilt (mom/trend/vol) | 11.42% | 0.78 | -17.17% | 79.39% | 44 |
| **multifactor_lowbeta (mom/trend/low_beta/vol)** | **6.69%** | **0.50** | **-22.41%** | 41.94% | 36 |
| momentum_only | 40.98% | 1.20 | -34.34% | 539.84% | 45 |
| SPY (buy & hold) | 14.28% | 0.88 | -25.36% | 105.68% | 0 |

**What each number means.** CAGR = annualized compound return. Sharpe =
return per unit of volatility (higher = better risk efficiency). MDD = worst
peak-to-trough fall (less negative = shallower, better). Turnover = cumulative
fraction of the book traded (cost proxy).

**The honest, surprising result: adding low-beta HURT in this window.**
`multifactor_lowbeta` was the *worst* of the sane schemes on every axis that
matters — lowest CAGR (6.69% vs composite_v1's 13.13%), lowest Sharpe (0.50),
and — most counterintuitively — a *deeper* drawdown (-22.4%) than the two
trend-following schemes (-16% to -17%), despite low-beta being marketed as a
drawdown-cushioning defensive factor. It beat SPY's drawdown (-25.4%) but by far
less than composite_v1 did, and gave up half the return to do it.

**Why did the BAB factor backfire here? Three compounding reasons:**

1. **The 2021–2026 regime punished low-beta.** This window was a mega-cap /
   AI-momentum melt-up. Low-beta names are by construction the ones *least* tied
   to that engine, so tilting toward them tilted *away* from the period's
   winners. This is not a bug — it is the well-documented poor decade for
   BAB/low-vol in US large-cap, and exactly the McLean-Pontiff "decayed,
   regime-dependent premium" the research warned about. A factor with a positive
   *long-run* premium can lag badly for a 5-year stretch.

2. **"Low beta" bought market-*uncorrelated* but individually *volatile* names.**
   Beta measures co-movement with the market, not total risk. The low-beta tilt
   selected energy and deep-value names (see the composite_v3 roster: APA, EOG,
   DVN, CF with *negative* beta) that are uncorrelated with the index because they
   move on oil/commodity cycles — but are highly volatile on their *own* factor.
   Low market beta ≠ low drawdown when the names carry large idiosyncratic risk.
   That is precisely why the drawdown got *worse*, not better.

3. **It diluted the trend protection that actually worked.** composite_v1's
   shallow -16% drawdown came from its trend gate + volatility penalty rotating
   into defensives during the 2022 sell-off. `multifactor_lowbeta` cut trend to
   0.15 and dropped liquidity entirely to fund the 0.25 low-beta sleeve, so it
   held lower-quality, market-uncorrelated names *through* drawdowns instead of
   de-risking. The cure it removed was more valuable than the cure it added.

**The takeaway is not "low-beta is useless"** — it is that a single factor's
contribution is **regime-dependent**, and this backtest is one (momentum-led)
regime. Low-beta is designed to earn its keep in a market *correction*, which
this window (outside the brief 2022 dip) did not sustain. The result is a
textbook reminder of the research's core caveat: the realistic prize from any
one factor tilt is small and conditional, and you only know the regime in
hindsight. It also underlines why the model **blends** factors rather than
betting on one — and why valuation + quality (the half this backtest *cannot*
test) are tracked forward rather than trusted from a single regime's history.

### 5.2 Forward-test: composite_v3 (out-of-sample, accruing)

Cohorts are recorded forward and measured vs SPY from stored prices — no
look-ahead. **A cohort under ~3 months carries little evidence**; the genesis
cohort started 2026-06-12. The harness exists precisely because valuation and
quality *cannot* be backtested (yfinance gives only latest fundamentals), so the
value/quality contribution to composite_v3 can only be earned in real time.

Run `python -m croesus.jobs.forward_test_run --evaluate --report` to read the
current track record.

---

## 6. Honest limitations

1. **Valuation and quality are forward-test only.** Backtesting them would need
   point-in-time fundamentals (Compustat-style); using today's statements for the
   past is severe look-ahead. So the backtest validates only the *price* factors
   (incl. low-beta); the fundamental half of composite_v3 is unproven until the
   forward-test accumulates months of cohorts.
2. **Survivorship bias** flatters every backtest scheme (today's index members),
   though not the *relative* comparison.
3. **Factor decay.** Published premia are ~58% weaker post-publication
   (McLean-Pontiff). Expect a long-only retail tilt to capture perhaps ⅓–½ of the
   paper premium after costs — the realistic prize is risk-adjusted improvement,
   not a headline return.
4. **Low-beta's negative-beta names** (energy, insurers here) reflect the trailing
   window's correlations; beta is unstable and regime-dependent.
