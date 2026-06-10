# Performance and Goal Tracking

Sprint 006d turns the investor profile's *target* return into a measurable
progress report. Earlier sprints can tell you what is overexposed or what to
rebalance; this layer answers a different question:

> Am I actually on track for the return I said I wanted — and at what risk?

```text
Portfolio Snapshots + Transactions + Investor Profile
  -> Contribution-Adjusted Return
  -> Goal Progress  (vs. expected_annual_return)
  -> Risk Status    (concentration / drift / drawdown)
  -> Attribution
```

It is a **reporting** layer only. It never trades, never places an order, and
never mutates holdings or the ledger. **Target returns are goals, not
guarantees** — every CLI line, report, and stored row carries that caveat.

## Running the check

```bash
python -m croesus.jobs.performance_check --date 2026-06-11 --report
```

| Flag | Effect |
|---|---|
| `--portfolio-id ID` | Portfolio to check (default: `default`). |
| `--date YYYY-MM-DD` | As-of date (default: today). |
| `--period P` | Restrict to one period (repeatable; default: all standard periods). |
| `--report` | Also write Markdown + CSV under `reports/`. |

The callable use case behind the CLI:

```python
from croesus.jobs.performance_check import run_performance_check
result = run_performance_check(conn, portfolio_id="default")  # PerformanceCheckResult
```

`PerformanceCheckResult.periods` is a list of structured rows ready for
dashboard cards; the CLI and the report are just formatted views of the same
data.

## Periods

`1m`, `3m`, `6m`, `1y`, and `since_inception`. A finite period starts at the
nearest portfolio snapshot **on or before** its boundary; `since_inception`
starts before the first dollar (start value 0, every contribution counted). A
period with no usable start or end snapshot is reported as
`insufficient_history` — never with a fabricated return number.

## Contribution-adjusted return

Deposits are not investment gain:

```text
investment_return     = end_value - start_value - net_contributions
investment_return_pct = investment_return / (start_value + net_contributions)
```

`net_contributions` is deposits minus withdrawals **only** — dividends, fees,
buys, and sells are internal and are not contributions. The annualized return
(`(1 + r) ** (365 / days) - 1`) drives the goal comparison; windows shorter than
20 days are not annualized. This is a first-pass approximation — time- or
money-weighted return can replace the internals later without changing the
result shape.

## Goal status

The annualized return is compared to the profile's `expected_annual_return`:

| Status | Meaning |
|---|---|
| `ahead_of_goal` | annualized return is more than 2pp above target |
| `near_goal` | within ±2pp of target |
| `behind_goal` | more than 2pp below target |
| `insufficient_history` | history too short to annualize, or no target set |

## Risk status

Shown beside the return so a gain cannot hide risk:

| Status | Trigger |
|---|---|
| `over_budget` | any concentration violation, or drawdown at/over the profile's `max_tolerable_drawdown` |
| `watch` | policy drift outside band, or drawdown within 80% of tolerance |
| `within_budget` | none of the above |
| `unknown` | no snapshot available to assess |

Concentration violations and policy drift are point-in-time at the latest
snapshot; trailing drawdown is per-period. Drawdown is computed on raw snapshot
values, so contributions within a window distort it — a known first-pass
limitation.

## Attribution

A lightweight, approximate decomposition of each period's value change. The
buckets sum to `end_value - start_value`:

```text
net_contributions + market_movement + realized + dividends
```

`market_movement` is the residual (unrealized mark change), so any
reconciliation gap between snapshot values and the transaction-derived figures
lands there. This is not benchmark- or factor-relative attribution.

## `portfolio_performance_snapshots`

One row per `(portfolio_id, as_of_date, period)`: `start_value`, `end_value`,
`net_contributions`, `investment_return`, `investment_return_pct`,
`target_return_pct`, `return_gap_pct`, `max_drawdown_pct`, `risk_status`,
`status`, and `metadata` (annualized return + attribution).

## Out of scope

Return guarantees, tax-aware performance, GIPS-compliant reporting,
benchmark-relative or factor attribution, and broker synchronization.
