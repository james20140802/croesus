# ADR 0009: CLI-first, app-ready boundaries

## Status

Accepted

## Context

Croesus starts as a Python-first local CLI because that is the fastest way to
build and verify the deterministic portfolio engine. However, the intended
personal product experience should not remain a collection of manual commands.
The system should be able to grow into a local web UI or desktop app without
rewriting the investment logic.

This is still a local-first product direction, not a cloud SaaS direction. The
expected future shape is:

```text
DuckDB local store
  + reusable service/use-case layer
  + local scheduler/freshness state
  + optional local API
  + local web UI or desktop app
```

## Decision

Croesus will be CLI-first but app-ready.

CLI jobs are entrypoints, not ownership boundaries. Business logic must live in
reusable functions and domain modules that can be called by CLI, tests, a local
API, a scheduler, or a future UI.

Every user-facing workflow should expose a callable use-case function that
returns structured results before formatting text output. Examples:

```text
run_profile_init(...)
run_portfolio_snapshot(...)
run_screening_job(...)
run_rebalance_check(...)
run_local_sync(...)
record_transaction(...)
```

Reports are user-facing artifacts, but reports are not the only product state.
Actions, warnings, freshness, violations, candidates, and approvals must be
stored or returned as structured data.

## Consequences

- CLI code should remain thin: parse arguments, call a use-case, print a
  summary, and map expected errors to stable exit codes.
- Domain logic should not depend on terminal prompts, stdout formatting, or
  Markdown generation.
- Markdown/CSV reports should be generated from structured results, not by
  recomputing logic inside report modules.
- Data freshness and job history must be queryable by a future dashboard.
- Proposed actions and approval state must be persisted before any execution
  workflow is introduced.
- CSV import remains useful for bootstrap and reconciliation, but normal app
  usage should move toward forms backed by transaction and holdings models.

## Non-goals

- Build the web UI before the portfolio engine is credible.
- Introduce cloud hosting or multi-user authentication.
- Add broker execution before explicit approval workflows exist.
- Treat the CLI output format as a stable API.
