# Sprint 006d: Performance and Goal Tracking

## Goal

Help the user understand whether the portfolio is progressing toward the
profile's target return while staying inside the profile's risk limits.

This sprint does not promise that the target return will be achieved. It turns
the user's target into a measurable progress report and exposes the gap between
return ambition, actual results, and current risk.

```text
Portfolio Snapshots
  + Transactions
  + Prices
  + Investor Profile
  -> Contribution-Adjusted Returns
  -> Goal Progress
  -> Risk Status
  -> Attribution
```

## Why This Exists

The current roadmap can compute prices, factors, macro state, exposure, drift,
and rebalancing proposals. That is necessary but not sufficient for a user who
asks:

> Am I actually on track for the return I said I wanted?

Without a goal-progress layer, Croesus can say what is overexposed or what to
rebalance, but it cannot explain whether the portfolio is moving toward the
user's stated objective.

## Scope

### 1. Performance Snapshot

Add a persisted performance view or table.

Suggested schema:

```sql
CREATE TABLE IF NOT EXISTS portfolio_performance_snapshots (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  period TEXT NOT NULL,
  start_value DOUBLE,
  end_value DOUBLE,
  net_contributions DOUBLE,
  investment_return DOUBLE,
  investment_return_pct DOUBLE,
  target_return_pct DOUBLE,
  return_gap_pct DOUBLE,
  max_drawdown_pct DOUBLE,
  risk_status TEXT,
  status TEXT,
  metadata JSON,
  PRIMARY KEY (portfolio_id, as_of_date, period)
);
```

Initial periods:

- `1m`
- `3m`
- `6m`
- `1y`
- `since_inception`

### 2. Contribution-Adjusted Return

Do not confuse deposits with investment performance.

Initial formula:

```text
investment_return = end_value - start_value - net_contributions
investment_return_pct = investment_return / adjusted_start_value
```

The first implementation may use a simple approximation. Time-weighted return
or money-weighted return can be added later once transaction history is richer.

### 3. Goal Progress

Compare performance against `investor_profiles.expected_annual_return`.

Example status values:

```text
ahead_of_goal
near_goal
behind_goal
insufficient_history
```

Example user-facing output:

```text
Goal progress: behind goal
- Target annual return: 10.0%
- 6-month annualized investment return: 7.8%
- Return gap: -2.2%
- Risk status: over budget
```

### 4. Risk Status

Return progress must be shown next to risk. A portfolio can be ahead of goal
while taking too much concentration or drawdown risk.

Initial risk statuses:

```text
within_budget
watch
over_budget
unknown
```

Inputs:

- current exposure violations;
- policy drift status;
- trailing drawdown when enough snapshot history exists;
- profile limits.

### 5. Attribution

Add lightweight attribution to explain what changed.

Initial buckets:

- market movement;
- net deposits/withdrawals;
- realized transactions;
- cash drag;
- concentration or sleeve drift notes.

Attribution should be approximate and explicit about limitations in the first
version.

### 6. App-Ready Result

Expose a callable use case:

```python
def run_performance_check(
    conn,
    *,
    portfolio_id: str = "default",
    as_of_date: date | None = None,
    periods: list[str] | None = None,
    log=print,
) -> PerformanceCheckResult:
    ...
```

The result should contain structured rows for dashboard cards and reports. CLI
output should be a formatted view of the same result.

## Suggested Files

```text
croesus/portfolio/
  performance.py
  performance_repository.py

croesus/jobs/
  performance_check.py

croesus/reports/
  performance.py
```

Tests:

```text
tests/test_performance.py
tests/test_performance_report.py
```

## Acceptance Criteria

- Performance snapshots can be computed from portfolio snapshots and
  transactions.
- Contribution-adjusted return does not treat deposits as investment gain.
- Goal status compares actual progress to `expected_annual_return`.
- Risk status is shown beside return status.
- Insufficient history produces a clear `insufficient_history` status, not a
  misleading return number.
- Output explicitly says target returns are goals, not guarantees.
- Structured result data is available for a future local dashboard.

## Out of Scope

- Return guarantees.
- Tax-aware performance.
- Full GIPS-compliant performance reporting.
- Benchmark-relative attribution.
- Sophisticated factor attribution.
- Broker synchronization.
