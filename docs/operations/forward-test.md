# Forward-Test Harness — honest evaluation of valuation-based schemes

The backtest (`croesus/backtest/`) deliberately **excludes valuation**: yfinance
gives only the latest financial statements, so scoring history with today's
fundamentals is look-ahead. That leaves the most important question — *does a
value tilt actually help?* — unanswerable by backtest.

The forward-test harness answers it the only honest way: record what each
candidate scheme would buy **today**, then measure realized return **forward**
from stored prices vs SPY. Every figure is out-of-sample. The cost is patience —
evidence accrues over months, not in one run.

## Schemes (`croesus/forward_test/schemes.py`)

- **composite_live** — today's live macro base weights (valuation already 0.10).
  The baseline a value tilt must beat.
- **composite_v2_value** — valuation raised 0.10 → 0.30, funded from momentum and
  liquidity. Value is negatively correlated with momentum, so the blend should
  lower risk; whether it also lifts return is what this harness measures.
- **composite_v3_multifactor** — the completed multi-factor blend: momentum 0.25
  + valuation 0.25 + quality 0.20 + low-beta 0.15 + trend 0.15. Combines the
  value/momentum negative correlation with the "control your junk" quality screen
  and the low-beta (BAB) defensive tilt — the levers the institutional-alpha
  research flagged as most defensible for a no-leverage long-only system. This is
  the scheme the forward-test exists to validate.
- **momentum_aggressive** — concentrated momentum (0.85) for a risk-tolerant
  user. `momentum_only` posted the highest raw backtest return *and* the deepest
  drawdown; this tracks its **realized** drawdown live before any capital follows
  it. Opt-in, never a default.

Each cohort holds the top `COHORT_TOP_N` (10) names, redundancy-group-capped
exactly like the backtest (a share-class pair never takes two slots).

## Usage

```bash
# Record today's cohort for every scheme — run periodically (e.g. monthly) to
# build the track record. Re-recording a (scheme, date) replaces it.
python -m croesus.jobs.forward_test_run --record

# One scheme only:
python -m croesus.jobs.forward_test_run --record --scheme composite_v2_value

# Evaluate every recorded cohort to date and write the report:
python -m croesus.jobs.forward_test_run --evaluate --report
```

Reports land in `reports/forward_test/<date>/` and register in the `reports`
table. Cohorts persist in `forward_test_cohorts` (one row per pick: entry price,
construction weight, rank, score).

## Reading the results honestly

- **Age is everything.** A cohort under ~3 months carries almost no evidence; the
  report labels every cohort's `days` and says so. Do not read a verdict from
  days of data.
- **No look-ahead, but small sample.** Real out-of-sample returns, but few
  cohorts at first — a single cohort's excess is mostly noise. Signal emerges
  only as independent monthly cohorts accumulate.
- **Survivorship still applies** to the universe (today's index members), same as
  the backtest — it flatters all schemes, not the relative comparison.

## Why this is not in `local_sync`

The forward-test is an **experiment track**, not the production pipeline. It is
human-run on purpose: recording a cohort is a deliberate "start the clock on this
hypothesis" act. It never trades and never feeds portfolio construction.
