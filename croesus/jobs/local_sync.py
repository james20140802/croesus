"""
Local sync orchestrator (Sprint 006b).

A single command — ``python -m croesus.jobs.local_sync`` — inspects data
freshness, runs only the jobs that are *due* in dependency order, isolates
failures, and records every run in ``job_runs``/``data_freshness`` so a future
local API or dashboard reads the same state the CLI does.

This orchestrator never executes trades or broker operations; it only refreshes
local research data. Jobs are injected as ``SyncJob`` values, which makes the
control flow (due detection, ordering, failure isolation) testable without
touching the network. ``default_sync_jobs()`` wires the real entrypoints.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence
from uuid import uuid4

import duckdb

from croesus.db.connection import get_connection, resolve_db_path
from croesus.db.migrate import migrate
from croesus.jobs.run_status import (
    RUN_FAILED,
    RUN_SKIPPED,
    RUN_SUCCESS,
    FreshnessState,
    RunStatusRepository,
)


class SyncSkip(Exception):
    """Raised by a job runner to signal a graceful, non-failure skip.

    Use this when a job genuinely has nothing to do (e.g. no holdings file is
    configured for ``portfolio_snapshot``). The message becomes the skip reason.
    Unlike an up-to-date skip, a SyncSkip marks the job unsatisfied so dependent
    jobs are skipped rather than run against stale prerequisites.
    """


# A runner receives the resolved db path and returns a one-line summary, raises
# SyncSkip to skip gracefully, or raises any other exception to fail.
JobRunner = Callable[[Path], str]


@dataclass(frozen=True)
class SyncJob:
    name: str
    domains: tuple[str, ...]
    runner: JobRunner
    depends_on: tuple[str, ...] = field(default_factory=tuple)
    # Soft trigger: when one of these jobs RAN successfully this cycle, this
    # job runs too — but unlike depends_on, their failure or skip never blocks
    # this job. Use for "B should react to A's changes" without coupling B's
    # availability to A's (e.g. fresh universe constituents need prices, yet a
    # Wikipedia outage must not stop daily price ingestion).
    soft_depends_on: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class JobOutcome:
    job_name: str
    status: str  # success | failed | skipped
    started_at: datetime
    finished_at: datetime
    summary: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class LocalSyncResult:
    run_id: str
    started_at: datetime
    finished_at: datetime
    outcomes: list[JobOutcome]
    freshness: list[FreshnessState]

    def outcome(self, job_name: str) -> JobOutcome | None:
        for o in self.outcomes:
            if o.job_name == job_name:
                return o
        return None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def run_local_sync(
    db_path: str | Path | None = None,
    *,
    jobs: list[SyncJob] | None = None,
    now: datetime | None = None,
    clock: Callable[[], datetime] | None = None,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> LocalSyncResult:
    """Run all due jobs in dependency order and record run/freshness state.

    ``now`` fixes the reference time for freshness evaluation; ``clock`` stamps
    individual job start/finish times (both injectable for deterministic tests).
    A job runs when ``force`` is set, when any of its domains is not fresh, or
    when one of its dependencies actually ran this cycle. A dependency that
    *failed* or *gracefully skipped* causes its dependents to skip; a dependency
    that was merely up-to-date does not.
    """
    resolved = resolve_db_path(db_path)
    clock = clock or _now_utc
    now = now or clock()
    migrate(resolved)

    jobs = jobs if jobs is not None else default_sync_jobs()
    run_id = f"local-sync-{now:%Y%m%dT%H%M%S}-{uuid4().hex[:6]}"
    started_overall = clock()

    # Initial freshness snapshot drives the "is it due?" decision.
    with get_connection(resolved) as conn:
        initial = {s.domain: s for s in RunStatusRepository(conn).refresh_freshness(now)}

    outcomes: list[JobOutcome] = []
    ran_ok: set[str] = set()
    unsatisfied: set[str] = set()  # failed or gracefully-skipped (blocks dependents)

    for job in jobs:
        started = clock()
        blocking = [d for d in job.depends_on if d in unsatisfied]
        if blocking:
            reason = f"dependency not satisfied: {', '.join(blocking)}"
            unsatisfied.add(job.name)
            _record(resolved, run_id, job, started, started, RUN_SKIPPED, reason, None)
            outcomes.append(_outcome(job, RUN_SKIPPED, started, started, reason, None))
            log(f"{job.name}: skipped — {reason}")
            continue

        domains_due = any(
            (initial.get(d) is None) or initial[d].is_due for d in job.domains
        )
        dep_refreshed = any(
            d in ran_ok for d in (*job.depends_on, *job.soft_depends_on)
        )
        if not (force or domains_due or dep_refreshed):
            reason = "up to date"
            _record(resolved, run_id, job, started, started, RUN_SKIPPED, reason, None)
            outcomes.append(_outcome(job, RUN_SKIPPED, started, started, reason, None))
            log(f"{job.name}: skipped — {reason}")
            continue  # up-to-date skip does NOT block dependents

        try:
            summary = job.runner(resolved)
            status, error = RUN_SUCCESS, None
            ran_ok.add(job.name)
        except SyncSkip as exc:
            status, summary, error = RUN_SKIPPED, str(exc), None
            unsatisfied.add(job.name)
        except Exception as exc:  # isolate failure; later jobs still get a chance
            status, summary, error = RUN_FAILED, None, f"{type(exc).__name__}: {exc}"
            unsatisfied.add(job.name)

        finished = clock()
        _record(resolved, run_id, job, started, finished, status, summary, error)
        outcomes.append(_outcome(job, status, started, finished, summary, error))
        log(f"{job.name}: {status}" + (f" — {summary or error}" if (summary or error) else ""))

    # Re-stamp every domain once, against a single end-of-run reference time, so
    # the persisted freshness is coherent (no per-job clock drift across domains).
    finished_overall = clock()
    with get_connection(resolved) as conn:
        repo = RunStatusRepository(conn)
        repo.refresh_freshness(finished_overall)
        freshness = repo.get_freshness()

    return LocalSyncResult(
        run_id=run_id,
        started_at=started_overall,
        finished_at=finished_overall,
        outcomes=outcomes,
        freshness=freshness,
    )


def _outcome(
    job: SyncJob,
    status: str,
    started: datetime,
    finished: datetime,
    summary: str | None,
    error: str | None,
) -> JobOutcome:
    return JobOutcome(
        job_name=job.name,
        status=status,
        started_at=started,
        finished_at=finished,
        summary=summary,
        error=error,
    )


def _record(
    db_path: Path,
    run_id: str,
    job: SyncJob,
    started: datetime,
    finished: datetime,
    status: str,
    summary: str | None,
    error: str | None,
) -> None:
    """Persist a single job_run row.

    The bookkeeping connection is opened and closed here — never held open while
    a job runner executes — so self-contained jobs that open their own DuckDB
    connection do not collide with the orchestrator on the same database file.
    Freshness is re-stamped once at the end of the run, not per job, so all
    domains share one reference time.
    """
    with get_connection(db_path) as conn:
        RunStatusRepository(conn).record_job_run(
            run_id=f"{run_id}:{job.name}",
            job_name=job.name,
            started_at=started,
            finished_at=finished,
            status=status,
            summary=summary,
            error=error,
            metadata={"domains": list(job.domains)},
        )


# ── Default production job wiring ────────────────────────────────────────────
# Runners import lazily to avoid import cost / cycles and so tests that inject
# their own jobs never trigger the real (network-touching) pipeline.

def _run_daily_macro(_db: Path) -> str:
    from croesus.jobs import daily_macro_run

    daily_macro_run.main()
    return "daily macro state refreshed"


def _run_weekly_macro(_db: Path) -> str:
    from croesus.jobs import weekly_macro_run

    weekly_macro_run.main()
    return "weekly macro state refreshed"


def _run_monthly_macro(_db: Path) -> str:
    from croesus.jobs import monthly_macro_run

    monthly_macro_run.main()
    return "monthly macro state refreshed"


def _run_daily(db: Path) -> str:
    from croesus.jobs.daily_run import run_daily_pipeline

    with get_connection(db) as conn:
        result = run_daily_pipeline(conn)
    return (
        f"prices={len(result.price_result.succeeded)} "
        f"fx={len(result.fx_result.succeeded)} "
        f"factors={len(result.factor_result.computed)} "
        f"valuation={len(result.valuation_result.computed)}"
    )


def _run_snapshot(db: Path) -> str:
    from croesus.jobs.portfolio_snapshot import NoHoldingsSource, run_portfolio_snapshot

    holdings_path = os.getenv("CROESUS_HOLDINGS_PATH")
    if holdings_path and not Path(holdings_path).exists():
        # A configured-but-missing CSV is a misconfiguration; falling back to
        # the ledger here could silently snapshot a different book.
        raise SyncSkip(f"configured holdings file not found: {holdings_path}")
    with get_connection(db) as conn:
        try:
            # No CSV configured → derive holdings from the transaction ledger.
            result = run_portfolio_snapshot(conn, holdings_path or None)
        except NoHoldingsSource as exc:
            raise SyncSkip(str(exc)) from exc
    return f"snapshot as_of={result.as_of_date.isoformat()}"


def _run_quarterly(db: Path) -> str:
    from croesus.jobs.quarterly_run import run_quarterly_pipeline

    with get_connection(db) as conn:
        result = run_quarterly_pipeline(conn)
    fr, vr = result.fundamentals_result, result.valuation_result
    return (
        f"fundamentals={len(fr.succeeded)} "
        f"dcf={len(vr.dcf_computed)} dcf_skipped={len(vr.dcf_skipped)}"
    )


def _run_performance_check(db: Path) -> str:
    from croesus.jobs.performance_check import run_performance_check

    with get_connection(db) as conn:
        result = run_performance_check(conn)
    return f"performance as_of={result.as_of_date.isoformat()}"


def _run_universe_refresh(db: Path) -> str:
    from croesus.jobs.universe_refresh import run_universe_refresh, summarize

    with get_connection(db) as conn:
        result = run_universe_refresh(conn)
    return f"universe {summarize(result)}"


def _run_disclosures(db: Path) -> str:
    from croesus.disclosures.ingest import ingest_disclosures

    with get_connection(db) as conn:
        result = ingest_disclosures(conn)
    return (
        f"disclosures ok={len(result.succeeded)} "
        f"skip={len(result.skipped)} fail={len(result.failed)}"
    )


def _run_disclosure_texts(db: Path) -> str:
    from croesus.disclosures.text_ingest import ingest_disclosure_texts

    with get_connection(db) as conn:
        result = ingest_disclosure_texts(conn)
    return (
        f"disclosure_texts fetched={len(result.fetched)} "
        f"skip={len(result.skipped)} defer={len(result.deferred)} "
        f"fail={len(result.failed)}"
    )


def _run_news_finnhub(db: Path) -> str:
    from croesus.news.finnhub_ingest import ingest_finnhub_news

    with get_connection(db) as conn:
        result = ingest_finnhub_news(conn)
    return (
        f"news_finnhub scanned={len(result.scanned)} "
        f"stored={result.stored} fail={len(result.failed)}"
    )


def _run_news_gdelt(db: Path) -> str:
    from croesus.news.gdelt_ingest import ingest_gdelt_news

    with get_connection(db) as conn:
        result = ingest_gdelt_news(conn)
    return (
        f"news_gdelt scanned={len(result.scanned)} "
        f"stored={result.stored} fail={len(result.failed)} "
        f"skipped={len(result.skipped)}"
    )


def _run_event_scan(db: Path) -> str:
    from croesus.events.scan import run_event_scan

    with get_connection(db) as conn:
        result = run_event_scan(conn)
    return (
        f"event_scan scanned={len(result.scanned)} "
        f"events={len(result.events)} fail={len(result.failed)}"
    )


def _run_screening(db: Path) -> str:
    from croesus.jobs.screening_run import run_screening_job
    from croesus.screening.report import save_report

    with get_connection(db) as conn:
        result = run_screening_job(conn)
        # The scheduled pipeline must leave the same artifact a manual
        # `screening_run --save-report` does — a ranking that exists only in
        # the DB is invisible on the status dashboard.
        md_path, _ = save_report(conn, result)
    return f"screening {result.run_id}: {len(result.candidates)} ranked ({md_path})"


def _run_rebalance(db: Path) -> str:
    from croesus.jobs.rebalance_check import run_rebalance_check

    with get_connection(db) as conn:
        result = run_rebalance_check(conn)
    return f"rebalance {result.decision}"


def default_sync_jobs() -> list[SyncJob]:
    """The real local pipeline, in dependency order (Sprint 006b §3)."""
    return [
        SyncJob("daily_macro_run", ("macro_daily",), _run_daily_macro),
        # Their own freshness thresholds (8d / 40d) gate how often these run;
        # without registration the macro_weekly/monthly domains sat permanently
        # at "missing" and dragged the dashboard verdict to STALE.
        SyncJob("weekly_macro_run", ("macro_weekly",), _run_weekly_macro),
        SyncJob("monthly_macro_run", ("macro_monthly",), _run_monthly_macro),
        # No depends_on (the weekly asset_universe threshold alone decides when
        # it is due), but ordered before daily_run so freshly registered index
        # constituents get their 1y price backfill in the same cycle.
        SyncJob("universe_refresh", ("asset_universe",), _run_universe_refresh),
        SyncJob(
            "disclosures_run", ("disclosures",), _run_disclosures,
            soft_depends_on=("universe_refresh",),
        ),
        SyncJob(
            "disclosure_texts_run", ("disclosure_texts",), _run_disclosure_texts,
            # Soft, not hard: the text job reads the disclosures TABLE, so it can
            # fetch text for already-stored filings even if today's metadata fetch
            # (disclosures_run) failed — a transient EDGAR error must not block it.
            soft_depends_on=("disclosures_run",),
        ),
        SyncJob(
            "news_finnhub_run", ("news_finnhub",), _run_news_finnhub,
            soft_depends_on=("universe_refresh",),
        ),
        SyncJob(
            "news_gdelt_run", ("news_gdelt",), _run_news_gdelt,
            soft_depends_on=("universe_refresh",),
        ),
        # soft_depends_on: a successful universe refresh forces a price run in
        # the same cycle (new constituents must not wait out the 48h prices
        # threshold), while a refresh failure leaves daily ingestion untouched.
        SyncJob(
            "daily_run", ("prices", "fx"), _run_daily,
            soft_depends_on=("universe_refresh",),
        ),
        SyncJob(
            "event_scan", ("events",), _run_event_scan,
            depends_on=("daily_run",),
            soft_depends_on=("disclosures_run",),
        ),
        # No depends_on: a dependency edge would re-run this every time
        # daily_run refreshes (i.e. daily). The quarterly freshness threshold
        # on the fundamentals domain alone decides when it is due; list order
        # still guarantees prices exist before the DCF runs.
        SyncJob("quarterly_run", ("fundamentals",), _run_quarterly),
        SyncJob(
            "portfolio_snapshot", ("portfolio_snapshot",), _run_snapshot,
            depends_on=("daily_run",),
        ),
        SyncJob(
            "screening_run", ("screening",), _run_screening,
            depends_on=("daily_run", "daily_macro_run"),
        ),
        SyncJob(
            "rebalance_check", ("rebalance_report",), _run_rebalance,
            depends_on=("portfolio_snapshot", "screening_run", "daily_macro_run"),
        ),
        SyncJob(
            "performance_check", ("performance",), _run_performance_check,
            depends_on=("portfolio_snapshot",),
        ),
    ]


# ── Local scheduling templates (Sprint 006b §4) ──────────────────────────────
# These only *render* a command/template; they never install a system service.

def render_cron_line(*, hour: int = 7, minute: int = 0, python: str | None = None) -> str:
    """Return a crontab line that runs local_sync daily at the given time."""
    py = python or sys.executable
    cwd = Path.cwd()
    return f"{minute} {hour} * * * cd {cwd} && {py} -m croesus.jobs.local_sync"


def render_launchd_plist(
    *,
    label: str = "com.croesus.local-sync",
    hour: int = 7,
    minute: int = 0,
    python: str | None = None,
) -> str:
    """Return a macOS launchd plist that runs local_sync daily at the given time."""
    py = python or sys.executable
    cwd = Path.cwd()
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{py}</string>
    <string>-m</string>
    <string>croesus.jobs.local_sync</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{cwd}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{cwd}/storage/local-sync.log</string>
  <key>StandardErrorPath</key>
  <string>{cwd}/storage/local-sync.err.log</string>
</dict>
</plist>
"""


VERDICT_READY = "READY"
VERDICT_DEGRADED = "DEGRADED"
VERDICT_STALE = "STALE"


def build_status_summary(
    conn: duckdb.DuckDBPyConnection,
    states: list[FreshnessState],
) -> list[str]:
    """Assemble the dashboard lines appended after the freshness table.

    Pure-ish: reads from ``conn`` but never writes.  Returns a list of
    human-readable lines for the caller to print or assert against in tests.

    Verdict rules (precedence order):
    - DEGRADED: any data-quality ERROR in the last 48 h.
    - STALE:    any freshness domain is due (no errors).
    - READY:    no errors and all domains are up to date.
    """
    from croesus.portfolio.approvals import list_pending_approvals
    from croesus.quality.repository import DataQualityRepository
    from croesus.reports.registry import latest_reports

    lines: list[str] = []

    # ── Latest reports ────────────────────────────────────────────────────────
    lines.append("Latest reports:")
    reports = latest_reports(conn)
    if reports:
        for r in reports:
            as_of = r.as_of_date.isoformat() if r.as_of_date else "—"
            lines.append(f"  {r.report_type:<20} as_of={as_of}  {r.path}")
    else:
        lines.append("  (none registered)")

    # ── Data-quality errors ───────────────────────────────────────────────────
    error_count = DataQualityRepository(conn).error_count(hours=48.0)
    lines.append(f"Data quality: {error_count} error(s) in last 48h")

    # ── Pending approvals ────────────────────────────────────────────────────
    pending = list_pending_approvals(conn)
    lines.append(f"Approvals: {len(pending)} pending")

    # ── Overall verdict ───────────────────────────────────────────────────────
    if error_count > 0:
        verdict = VERDICT_DEGRADED
    elif any(s.is_due for s in states):
        verdict = VERDICT_STALE
    else:
        verdict = VERDICT_READY
    lines.append(f"Overall: {verdict}")

    return lines


def _print_freshness(states: list[FreshnessState], log: Callable[[str], None]) -> None:
    log("Data freshness:")
    for s in states:
        data_date = s.latest_data_date.isoformat() if s.latest_data_date else "—"
        log(f"  {s.domain:<20} {s.status:<8} data={data_date}  ({s.reason})")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.local_sync",
        description="Refresh due local research data in dependency order.",
    )
    parser.add_argument(
        "--db-path", default=None,
        help="override the DuckDB path (exported so self-contained sub-jobs honor it)",
    )
    parser.add_argument(
        "--force", action="store_true", help="run every job regardless of freshness"
    )
    parser.add_argument(
        "--status", action="store_true",
        help="print current data freshness and exit (no jobs run)",
    )
    parser.add_argument(
        "--print-cron", action="store_true", help="print a crontab line and exit"
    )
    parser.add_argument(
        "--print-launchd", action="store_true", help="print a launchd plist and exit"
    )
    parser.add_argument("--hour", type=int, default=7, help="schedule hour (templates)")
    parser.add_argument("--minute", type=int, default=0, help="schedule minute (templates)")
    args = parser.parse_args(argv)

    # Some sub-jobs (e.g. the macro runs) manage their own connection via
    # resolve_db_path(), which reads CROESUS_DB_PATH. Export the override so a
    # custom --db-path reaches every runner, not just the conn-accepting ones.
    if args.db_path:
        os.environ["CROESUS_DB_PATH"] = str(args.db_path)

    if args.print_cron:
        print(render_cron_line(hour=args.hour, minute=args.minute))
        return 0
    if args.print_launchd:
        print(render_launchd_plist(hour=args.hour, minute=args.minute))
        return 0

    if args.status:
        resolved = resolve_db_path(args.db_path)
        migrate(resolved)
        with get_connection(resolved) as conn:
            states = RunStatusRepository(conn).refresh_freshness(_now_utc())
            _print_freshness(states, print)
            for line in build_status_summary(conn, states):
                print(line)
        return 0

    result = run_local_sync(db_path=args.db_path, force=args.force)
    print(f"local sync {result.run_id}:")
    for outcome in result.outcomes:
        detail = outcome.summary or outcome.error or ""
        print(f"  {outcome.job_name:<20} {outcome.status:<8} {detail}")
    _print_freshness(result.freshness, print)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
