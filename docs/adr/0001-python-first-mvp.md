# ADR 0001: Python-First MVP

## Status

Accepted

## Context

Croesus requires data ingestion, financial time-series processing, factor computation, screening, reporting, and eventually LLM-assisted qualitative research.

The first implementation decision is whether to start with a web application or with a data/research core.

A web application would be useful later, but it is not the main risk in the early stage. The main risk is whether Croesus can reliably collect data, normalize assets, compute investment signals, and produce useful research outputs.

## Decision

Croesus will start as a Python-first package and CLI workflow.

The initial system should be runnable through commands such as:

```bash
python -m croesus.jobs.bootstrap
python -m croesus.jobs.daily_run
```

## Rationale

Python is the best initial fit because:

- Financial data processing is data-heavy.
- pandas, DuckDB, numpy, and related tools are mature.
- Factor computation is easier to prototype in Python.
- Backtesting and analytics will be easier to add later.
- The core logic can later be wrapped by an API or web UI.

## Consequences

### Positive

- Faster data pipeline development.
- Easier financial computation.
- Simpler local experimentation.
- Clear separation between core research logic and future UI.

### Negative

- No immediate web interface.
- User interaction is initially CLI/report-based.
- Future API boundary must be designed when a frontend is introduced.

## Follow-Up Decisions

- Decide when to add FastAPI or another API layer.
- Decide when to introduce a web dashboard.
- Decide how scheduled jobs should run in production.
