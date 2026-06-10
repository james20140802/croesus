# Local Scheduler and Data Freshness

Sprint 006b makes Croesus keep its local research data up to date without the
user remembering which jobs to run, and turns "can I trust today's report?" into
a queryable product state.

## One command

```bash
python -m croesus.jobs.local_sync
```

This inspects data freshness, runs only the jobs that are **due**, in dependency
order, isolates failures, and records the result. It **never executes trades or
broker operations** — it only refreshes local research data.

Dependency order:

```text
daily_macro_run
daily_run            (prices, fx, factors)
portfolio_snapshot   (only if CROESUS_HOLDINGS_PATH points to a holdings CSV)
screening_run
rebalance_check
```

Useful flags:

| Flag | Effect |
|---|---|
| `--status` | Print current freshness for every domain and exit (no jobs run). |
| `--force` | Run every job regardless of freshness. |
| `--db-path PATH` | Use a non-default DuckDB file. |
| `--print-cron` | Print a crontab line and exit. |
| `--print-launchd` | Print a macOS launchd plist and exit. |
| `--hour H` / `--minute M` | Schedule time used by the template flags. |

`portfolio_snapshot` is skipped gracefully when no holdings file is configured;
set `CROESUS_HOLDINGS_PATH` to a CSV to enable it.

## State tables

Both the CLI, a future local API, and a future dashboard read the same two
tables, so status is consistent everywhere.

### `job_runs`

One row per job execution: `run_id`, `job_name`, `started_at`, `finished_at`,
`status` (`success` / `failed` / `skipped`), `summary`, `error`, `metadata`.

### `data_freshness`

One row per data domain: `latest_data_date`, `latest_success_at`,
`stale_after_hours`, `status` (`fresh` / `stale` / `missing`), `reason`.

Domains and their staleness thresholds:

| Domain | Source job | Stale after |
|---|---|---|
| `prices` | `daily_run` | 48h |
| `fx` | `daily_run` | 48h |
| `macro_daily` | `daily_macro_run` | 36h |
| `macro_weekly` | `weekly_macro_run` | 8 days |
| `macro_monthly` | `monthly_macro_run` | 40 days |
| `portfolio_snapshot` | `portfolio_snapshot` | 48h |
| `screening` | `screening_run` | 8 days |
| `rebalance_report` | `rebalance_check` | 8 days |

A domain is `fresh` when its source job's last **successful** run is within the
threshold, `stale` when that run is older, and `missing` when no successful run
has ever been recorded. A domain that is not `fresh` is **due**.

## Scheduling (opt-in)

The first implementation only *renders* a command or template — it never installs
a system service. You install it yourself.

### cron

```bash
python -m croesus.jobs.local_sync --print-cron --hour 7 --minute 0
# 0 7 * * * cd /path/to/croesus && /path/to/python -m croesus.jobs.local_sync
```

Add the printed line with `crontab -e`.

### macOS launchd

```bash
python -m croesus.jobs.local_sync --print-launchd --hour 7 > ~/Library/LaunchAgents/com.croesus.local-sync.plist
launchctl load ~/Library/LaunchAgents/com.croesus.local-sync.plist
```

### App startup

A future local app can call `run_local_sync(...)` on launch to "sync now if
stale" instead of relying on an external scheduler.

## Out of scope

Cloud deployment, multi-user scheduling, push notifications, broker execution,
and the web UI itself are not part of this sprint.
