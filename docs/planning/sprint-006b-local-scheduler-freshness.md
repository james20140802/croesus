# Sprint 006b: Local Scheduler and Data Freshness

## Goal

Make the local portfolio OS keep itself up to date without requiring the user to
remember which CLI jobs to run each day.

```text
Run Registry
  -> Freshness Checks
  -> Due Job Selection
  -> Local Scheduler / Background Runner
  -> Status Output for Web/App
```

This sprint should follow Sprint 006 because `rebalance_check` creates the
first complete Level 1 output. It should be completed before investing heavily
in a local web UI so the dashboard can show reliable status instead of wrapping
manual CLI steps.

This sprint is a local app readiness sprint. It should make data freshness a
queryable product state, not just text printed after running commands.

## Scope

### 1. Run Status Schema

Add tables for job execution state:

```sql
CREATE TABLE IF NOT EXISTS job_runs (
  run_id TEXT PRIMARY KEY,
  job_name TEXT NOT NULL,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  status TEXT,
  summary TEXT,
  error TEXT,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS data_freshness (
  data_domain TEXT PRIMARY KEY,
  latest_data_date DATE,
  latest_success_at TIMESTAMP,
  stale_after_hours DOUBLE,
  status TEXT,
  reason TEXT,
  metadata JSON
);
```

Initial `data_domain` values:

- `prices`
- `fx`
- `macro_daily`
- `macro_weekly`
- `macro_monthly`
- `portfolio_snapshot`
- `screening`
- `rebalance_report`
- `fundamentals` once Sprint 007 is implemented

### 2. Freshness Policy

Define deterministic freshness rules:

| Domain | Expected Freshness |
|---|---|
| prices | latest market day or explicit stale warning |
| fx | latest market day for non-base currencies |
| macro_daily | daily |
| macro_weekly | weekly |
| macro_monthly | monthly |
| portfolio_snapshot | after holdings or prices change |
| screening | after factors or MacroState change |
| rebalance_report | after snapshot, screening, or MacroState change |

The app should be able to answer: "Can I trust today's report?"

Freshness state should be structured enough for dashboard cards:

```text
domain
status
latest_data_date
latest_success_at
reason
blocking_next_actions
```

### 3. Orchestrator

Add a local orchestrator:

```bash
python -m croesus.jobs.local_sync
```

Behavior:

1. Run migration.
2. Inspect latest successful job runs and data dates.
3. Determine which jobs are due.
4. Run due jobs in dependency order.
5. Record success, failure, and skipped jobs.
6. Never submit broker orders.

Example dependency order:

```text
daily_macro_run
daily_run
portfolio_snapshot (if holdings path/config is available)
screening_run
rebalance_check
```

### 4. Local Scheduling Hook

Provide a simple local-first scheduling option:

- launchd plist template for macOS; or
- cron-compatible command; or
- app startup "sync now if stale" behavior.

The first implementation may only generate the command/template. It should not
install system services without explicit user action.

## Suggested Files

```text
croesus/jobs/local_sync.py
croesus/jobs/run_status.py
croesus/db/schema.sql
docs/operations/local-scheduler.md
```

Tests:

```text
tests/test_local_sync.py
tests/test_data_freshness.py
```

## Acceptance Criteria

- Croesus records job success/failure history.
- A single `local_sync` command can update all due local data in dependency
  order.
- Freshness state is queryable for a future local API/web dashboard.
- Failures are isolated and surfaced clearly.
- The scheduler never executes trades or broker operations.
- CLI output, local API responses, and a future dashboard all read the same
  `job_runs` and `data_freshness` state.

## Out of Scope

- Cloud deployment.
- Multi-user scheduling.
- Push notifications.
- Broker execution.
- Web UI implementation.
