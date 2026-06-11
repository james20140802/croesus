from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.local_sync import (
    SyncJob,
    SyncSkip,
    default_sync_jobs,
    render_cron_line,
    render_launchd_plist,
    run_local_sync,
)
from croesus.jobs.run_status import DOMAINS_BY_NAME, RunStatusRepository

UTC = timezone.utc
NOW = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def _make_job(
    name: str,
    domains: list[str],
    calls: list[str],
    *,
    depends_on: tuple[str, ...] = (),
    raises: Exception | None = None,
) -> SyncJob:
    def runner(_db: Path) -> str:
        calls.append(name)
        if raises is not None:
            raise raises
        return f"{name} ok"

    return SyncJob(name, tuple(domains), runner, depends_on)


def _seed_success(db_path: Path, job_name: str, when: datetime) -> None:
    """Record a prior successful run so the mapped domain starts out fresh."""
    with get_connection(db_path) as conn:
        RunStatusRepository(conn).record_job_run(
            run_id=f"seed-{job_name}", job_name=job_name,
            started_at=when, finished_at=when, status="success",
        )


def test_runs_all_due_jobs_in_dependency_order(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.duckdb"
    calls: list[str] = []
    jobs = [
        _make_job("daily_run", ["prices"], calls),
        _make_job("screening_run", ["screening"], calls, depends_on=("daily_run",)),
    ]
    result = run_local_sync(
        db_path, jobs=jobs, now=NOW, clock=lambda: NOW, log=lambda *_: None
    )
    assert calls == ["daily_run", "screening_run"]
    assert result.outcome("daily_run").status == "success"
    assert result.outcome("screening_run").status == "success"


def test_skips_job_that_is_already_fresh(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.duckdb"
    migrate(db_path)
    _seed_success(db_path, "daily_run", NOW)  # makes domain "prices" fresh

    calls: list[str] = []
    jobs = [_make_job("daily_run", ["prices"], calls)]
    result = run_local_sync(
        db_path, jobs=jobs, now=NOW, clock=lambda: NOW, log=lambda *_: None
    )
    assert calls == []
    assert result.outcome("daily_run").status == "skipped"
    assert result.outcome("daily_run").summary == "up to date"


def test_force_runs_even_when_fresh(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.duckdb"
    migrate(db_path)
    _seed_success(db_path, "daily_run", NOW)

    calls: list[str] = []
    jobs = [_make_job("daily_run", ["prices"], calls)]
    run_local_sync(
        db_path, jobs=jobs, now=NOW, clock=lambda: NOW, force=True, log=lambda *_: None
    )
    assert calls == ["daily_run"]


def test_failure_is_isolated_and_blocks_only_dependents(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.duckdb"
    calls: list[str] = []
    jobs = [
        _make_job("daily_run", ["prices"], calls, raises=RuntimeError("network down")),
        _make_job("screening_run", ["screening"], calls, depends_on=("daily_run",)),
        _make_job("daily_macro_run", ["macro_daily"], calls),  # independent, still runs
    ]
    result = run_local_sync(
        db_path, jobs=jobs, now=NOW, clock=lambda: NOW, log=lambda *_: None
    )
    assert "daily_run" in calls
    assert "daily_macro_run" in calls
    assert "screening_run" not in calls  # dependent skipped

    assert result.outcome("daily_run").status == "failed"
    assert "network down" in result.outcome("daily_run").error
    assert result.outcome("screening_run").status == "skipped"
    assert "dependency not satisfied" in result.outcome("screening_run").summary
    assert result.outcome("daily_macro_run").status == "success"


def test_graceful_skip_blocks_dependents(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.duckdb"
    calls: list[str] = []
    jobs = [
        _make_job(
            "portfolio_snapshot", ["portfolio_snapshot"], calls,
            raises=SyncSkip("no holdings configured"),
        ),
        _make_job(
            "rebalance_check", ["rebalance_report"], calls,
            depends_on=("portfolio_snapshot",),
        ),
    ]
    result = run_local_sync(
        db_path, jobs=jobs, now=NOW, clock=lambda: NOW, log=lambda *_: None
    )
    assert calls == ["portfolio_snapshot"]
    assert result.outcome("portfolio_snapshot").status == "skipped"
    assert result.outcome("portfolio_snapshot").summary == "no holdings configured"
    assert result.outcome("rebalance_check").status == "skipped"


def test_up_to_date_skip_does_not_block_due_dependent(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.duckdb"
    migrate(db_path)
    _seed_success(db_path, "daily_run", NOW)  # "prices" fresh -> daily_run skipped

    calls: list[str] = []
    jobs = [
        _make_job("daily_run", ["prices"], calls),
        _make_job("screening_run", ["screening"], calls, depends_on=("daily_run",)),
    ]
    result = run_local_sync(
        db_path, jobs=jobs, now=NOW, clock=lambda: NOW, log=lambda *_: None
    )
    assert calls == ["screening_run"]  # dependent still runs (it is due)
    assert result.outcome("daily_run").status == "skipped"
    assert result.outcome("screening_run").status == "success"


def test_records_history_and_refreshes_freshness(tmp_path: Path) -> None:
    db_path = tmp_path / "sync.duckdb"
    calls: list[str] = []
    jobs = [_make_job("daily_run", ["prices"], calls)]
    result = run_local_sync(
        db_path, jobs=jobs, now=NOW, clock=lambda: NOW, log=lambda *_: None
    )

    fresh = {s.domain: s for s in result.freshness}
    assert fresh["prices"].status == "fresh"
    # Tie freshness to the recorded success, not an unconditional "fresh" write:
    # a domain whose job never ran must stay missing in the same result.
    assert fresh["prices"].latest_success_at == NOW
    assert fresh["screening"].status == "missing"
    assert fresh["screening"].latest_success_at is None

    with get_connection(db_path) as conn:
        runs = RunStatusRepository(conn).recent_job_runs("daily_run")
    assert runs and runs[0]["status"] == "success"


def test_default_jobs_are_recommendation_only_no_trades() -> None:
    names = [j.name for j in default_sync_jobs()]
    assert names == [
        "daily_macro_run",
        "daily_run",
        "quarterly_run",
        "portfolio_snapshot",
        "screening_run",
        "rebalance_check",
        "performance_check",
    ]
    # The scheduler must never wire an order-submission / execution job.
    assert not any(
        token in name
        for name in names
        for token in ("order", "execute", "broker", "submit")
    )


def test_default_job_names_match_their_domain_freshness_jobs() -> None:
    # Each default job's name must equal the job_name its domains are tracked
    # under; otherwise a successful run would never clear those domains' staleness.
    for job in default_sync_jobs():
        for domain in job.domains:
            spec = DOMAINS_BY_NAME[domain]
            assert spec.job_name == job.name, (
                f"job {job.name!r} refreshes domain {domain!r} whose freshness is "
                f"keyed to job {spec.job_name!r} — name drift would hide staleness"
            )


def test_scheduling_templates_render() -> None:
    cron = render_cron_line(hour=7, minute=30)
    assert "croesus.jobs.local_sync" in cron
    assert cron.startswith("30 7 ")

    plist = render_launchd_plist(label="com.example.test", hour=6, minute=15)
    assert "com.example.test" in plist
    assert "<plist" in plist
    assert "croesus.jobs.local_sync" in plist
