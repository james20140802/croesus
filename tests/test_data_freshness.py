from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.run_status import (
    STATUS_FRESH,
    STATUS_MISSING,
    STATUS_STALE,
    RunStatusRepository,
    evaluate_freshness,
)

UTC = timezone.utc


def _migrated(tmp_path: Path) -> Path:
    db_path = tmp_path / "freshness.duckdb"
    migrate(db_path)
    return db_path


def test_migrate_creates_run_status_tables(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
    assert {"job_runs", "data_freshness"} <= tables


def test_evaluate_freshness_missing_without_success() -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    status, reason = evaluate_freshness(now, None, None, 48.0)
    assert status == STATUS_MISSING
    assert "no successful run" in reason

    # Data present but the pipeline never recorded a success is still missing.
    status, _ = evaluate_freshness(now, date(2026, 6, 10), None, 48.0)
    assert status == STATUS_MISSING


def test_evaluate_freshness_fresh_within_threshold() -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    success = now - timedelta(hours=10)
    status, _ = evaluate_freshness(now, date(2026, 6, 11), success, 48.0)
    assert status == STATUS_FRESH


def test_evaluate_freshness_stale_beyond_threshold() -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    success = now - timedelta(hours=60)
    status, reason = evaluate_freshness(now, date(2026, 6, 8), success, 48.0)
    assert status == STATUS_STALE
    assert "exceeds" in reason


def test_evaluate_freshness_handles_naive_success_as_utc() -> None:
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    naive_success = datetime(2026, 6, 11, 6, 0)  # naive == UTC by contract
    status, _ = evaluate_freshness(now, date(2026, 6, 11), naive_success, 48.0)
    assert status == STATUS_FRESH


def test_latest_success_at_ignores_failed_runs(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        repo = RunStatusRepository(conn)
        good = datetime(2026, 6, 11, 6, 0, tzinfo=UTC)
        later_failed = datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
        repo.record_job_run(
            run_id="r1", job_name="daily_run",
            started_at=good, finished_at=good, status="success",
        )
        repo.record_job_run(
            run_id="r2", job_name="daily_run",
            started_at=later_failed, finished_at=later_failed, status="failed",
            error="boom",
        )
        latest = repo.latest_success_at("daily_run")
    assert latest == good


def test_compute_freshness_uses_source_dates_and_runs(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO prices_daily (asset_id, date, close) VALUES ('A', DATE '2026-06-10', 1.0)"
        )
        repo = RunStatusRepository(conn)
        repo.record_job_run(
            run_id="r1", job_name="daily_run",
            started_at=now - timedelta(hours=5), finished_at=now - timedelta(hours=5),
            status="success",
        )
        states = {s.domain: s for s in repo.compute_freshness(now)}

    prices = states["prices"]
    assert prices.status == STATUS_FRESH
    assert prices.latest_data_date == date(2026, 6, 10)
    # A domain with no source data and no run is missing.
    assert states["macro_monthly"].status == STATUS_MISSING


def test_screening_freshness_parses_run_id_date(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO screening_results (run_id, asset_id, score, rank, decision_bucket)
            VALUES ('screening-2026-06-09-abcd1234', 'A', 0.5, 1, 'candidate')
            """
        )
        repo = RunStatusRepository(conn)
        repo.record_job_run(
            run_id="r1", job_name="screening_run",
            started_at=now, finished_at=now, status="success",
        )
        states = {s.domain: s for s in repo.compute_freshness(now)}
    assert states["screening"].latest_data_date == date(2026, 6, 9)


def test_refresh_and_get_freshness_roundtrip(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    now = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    with get_connection(db_path) as conn:
        repo = RunStatusRepository(conn)
        repo.record_job_run(
            run_id="r1", job_name="daily_run",
            started_at=now, finished_at=now, status="success",
        )
        repo.refresh_freshness(now)

    # Re-open to prove it was persisted, not just held in memory.
    with get_connection(db_path) as conn:
        persisted = {s.domain: s for s in RunStatusRepository(conn).get_freshness()}
    assert persisted["prices"].status == STATUS_FRESH
    assert persisted["prices"].latest_success_at == now
