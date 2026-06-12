"""
Sprint 012: reports registry and --status dashboard tests.

Tests:
- register + latest_reports roundtrip; newest-per-type wins.
- write_portfolio_action_reports registers 2 rows (md + csv) with run_id.
- build_status_summary: DEGRADED when error, STALE when stale domain, READY
  when clean and fresh, pending-approval count appears.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pytest

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.local_sync import (
    VERDICT_DEGRADED,
    VERDICT_READY,
    VERDICT_STALE,
    build_status_summary,
)
from croesus.jobs.run_status import (
    STATUS_FRESH,
    STATUS_MISSING,
    FreshnessState,
    RunStatusRepository,
)
from croesus.portfolio.actions import ProposedAction
from croesus.portfolio.repository import PortfolioRepository
from croesus.quality.models import DataQualityIssue
from croesus.quality.repository import DataQualityRepository
from croesus.reports.portfolio_action import write_portfolio_action_reports
from croesus.reports.registry import (
    REPORT_TYPE_MACRO,
    REPORT_TYPE_PORTFOLIO_ACTION,
    REPORT_TYPE_SCREENING,
    RegisteredReport,
    latest_reports,
    register_many,
    register_report,
)

AS_OF = date(2026, 6, 1)
UTC = timezone.utc


# ── helpers ───────────────────────────────────────────────────────────────────

def _db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.duckdb"
    migrate(db_path)
    return db_path


def _action(
    action_id: str = "run-1-001",
    run_id: str = "run-1",
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        run_id=run_id,
        asset_id="US_EQ_NVDA",
        sleeve_name="satellite_equity",
        action_type="trim",
        current_weight=0.18,
        target_weight=0.10,
        proposed_weight=0.10,
        estimated_trade_value=8000.0,
        reason_codes=["POSITION_OVER_MAX"],
        human_readable_reason="Trim US_EQ_NVDA from 18.0% to 10.0%.",
        requires_research=False,
        requires_user_approval=True,
    )


def _fresh_state(domain: str = "prices") -> FreshnessState:
    return FreshnessState(
        domain=domain,
        status=STATUS_FRESH,
        latest_data_date=date(2026, 6, 1),
        latest_success_at=datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
        stale_after_hours=48.0,
        reason="ok",
    )


def _stale_state(domain: str = "prices") -> FreshnessState:
    return FreshnessState(
        domain=domain,
        status=STATUS_MISSING,
        latest_data_date=None,
        latest_success_at=None,
        stale_after_hours=48.0,
        reason="no data and no successful run recorded",
    )


# ── registry roundtrip ────────────────────────────────────────────────────────

def test_register_and_latest_reports_roundtrip(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        register_report(
            conn,
            report_type=REPORT_TYPE_MACRO,
            path="/reports/macro/2026-06-01/macro.md",
            as_of_date=date(2026, 6, 1),
        )
        reports = latest_reports(conn)

    assert len(reports) == 1
    r = reports[0]
    assert r.report_type == REPORT_TYPE_MACRO
    assert r.as_of_date == date(2026, 6, 1)
    assert r.path == "/reports/macro/2026-06-01/macro.md"
    assert r.fmt == "markdown"
    assert r.run_id is None


def test_newest_per_type_wins(tmp_path: Path) -> None:
    """The latest inserted row wins; the older one does not appear."""
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        register_report(
            conn,
            report_type=REPORT_TYPE_MACRO,
            path="/reports/macro/2026-06-01/macro.md",
            as_of_date=date(2026, 6, 1),
        )
        # Second registration for the same type but a later date.
        register_report(
            conn,
            report_type=REPORT_TYPE_MACRO,
            path="/reports/macro/2026-06-02/macro.md",
            as_of_date=date(2026, 6, 2),
        )
        reports = latest_reports(conn)

    # Only one row per type.
    assert len(reports) == 1
    assert reports[0].path == "/reports/macro/2026-06-02/macro.md"


def test_latest_reports_multiple_types(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        register_report(conn, report_type=REPORT_TYPE_MACRO, path="/r/macro.md")
        register_report(conn, report_type=REPORT_TYPE_SCREENING, path="/r/screen.csv")
        reports = latest_reports(conn)

    types = {r.report_type for r in reports}
    assert REPORT_TYPE_MACRO in types
    assert REPORT_TYPE_SCREENING in types


def test_register_many_creates_separate_rows(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        register_many(
            conn,
            REPORT_TYPE_SCREENING,
            ["/r/screen.md", "/r/screen.csv"],
            as_of_date=date(2026, 6, 1),
            run_id="screen-run-1",
        )
        # All rows in the table:
        count = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]

    assert count == 2


def test_format_inferred_from_suffix(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        register_report(conn, report_type="test", path="/r/file.csv")
        rows = conn.execute("SELECT format FROM reports").fetchall()

    assert rows[0][0] == "csv"


def test_empty_registry_returns_empty_list(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        reports = latest_reports(conn)
    assert reports == []


# ── portfolio action report integration ──────────────────────────────────────

def test_write_portfolio_action_reports_registers_two_rows(tmp_path: Path) -> None:
    """After write_portfolio_action_reports there is one md row and one csv row."""
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-1", "default", "default", AS_OF,
            decision="rebalance_recommended", summary="1 action.",
            macro_regime="Goldilocks", macro_positioning="Neutral",
            metadata={"latest_portfolio_snapshot_date": "2026-06-01"},
        )
        repo.replace_proposed_actions("run-1", [_action()])
        md_path, csv_path = write_portfolio_action_reports(
            conn, "run-1", reports_dir=tmp_path
        )
        rows = conn.execute(
            "SELECT report_type, format, run_id FROM reports ORDER BY format"
        ).fetchall()

    assert len(rows) == 2
    types = {r[0] for r in rows}
    assert types == {REPORT_TYPE_PORTFOLIO_ACTION}
    formats = {r[1] for r in rows}
    assert formats == {"csv", "markdown"}
    # All rows carry the run_id.
    run_ids = {r[2] for r in rows}
    assert run_ids == {"run-1"}


# ── build_status_summary ─────────────────────────────────────────────────────

def test_status_summary_ready_when_no_errors_and_fresh(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        states = [_fresh_state("prices")]
        lines = build_status_summary(conn, states)

    verdicts = [l for l in lines if l.startswith("Overall:")]
    assert len(verdicts) == 1
    assert VERDICT_READY in verdicts[0]


def test_status_summary_degraded_when_error_row_exists(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        # Insert an ERROR-severity data-quality issue.
        DataQualityRepository(conn).record_many(
            [
                DataQualityIssue(
                    domain="fx",
                    severity="error",
                    code="FX_MISSING",
                    message="no USD/KRW rate",
                    asset_id=None,
                    currency="KRW",
                    as_of_date=date(2026, 6, 1),
                )
            ]
        )
        states = [_fresh_state("prices")]
        lines = build_status_summary(conn, states)

    verdicts = [l for l in lines if l.startswith("Overall:")]
    assert VERDICT_DEGRADED in verdicts[0]
    # The error count line should report 1.
    error_lines = [l for l in lines if "error(s)" in l]
    assert error_lines
    assert "1 error" in error_lines[0]


def test_status_summary_stale_when_domain_is_due(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        # No data-quality errors, but prices domain is stale/missing.
        states = [_stale_state("prices")]
        lines = build_status_summary(conn, states)

    verdicts = [l for l in lines if l.startswith("Overall:")]
    assert VERDICT_STALE in verdicts[0]


def test_status_summary_degraded_takes_precedence_over_stale(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        DataQualityRepository(conn).record_many(
            [
                DataQualityIssue(
                    domain="portfolio_snapshot",
                    severity="error",
                    code="PRICE_MISSING",
                    message="NVDA price missing",
                    asset_id="US_EQ_NVDA",
                    currency=None,
                    as_of_date=date(2026, 6, 1),
                )
            ]
        )
        # Also stale.
        states = [_stale_state("prices")]
        lines = build_status_summary(conn, states)

    verdicts = [l for l in lines if l.startswith("Overall:")]
    assert VERDICT_DEGRADED in verdicts[0]


def test_status_summary_reports_none_registered(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        lines = build_status_summary(conn, [_fresh_state()])

    report_lines = [l for l in lines if "(none registered)" in l]
    assert report_lines


def test_status_summary_shows_registered_report(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        register_report(
            conn,
            report_type=REPORT_TYPE_MACRO,
            path="/reports/macro/2026-06-01/macro.md",
            as_of_date=date(2026, 6, 1),
        )
        lines = build_status_summary(conn, [_fresh_state()])

    report_lines = [l for l in lines if REPORT_TYPE_MACRO in l]
    assert report_lines
    assert "/reports/macro/2026-06-01/macro.md" in report_lines[0]


def test_status_summary_pending_approvals_count(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        # Insert a rebalance run with an approvable action.
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-1", "default", "default", AS_OF,
            decision="rebalance_recommended", summary="test", metadata={},
        )
        repo.replace_proposed_actions("run-1", [_action()])
        lines = build_status_summary(conn, [_fresh_state()])

    approval_lines = [l for l in lines if "Approvals:" in l]
    assert approval_lines
    assert "1 pending" in approval_lines[0]


def test_status_summary_approval_zero_when_none_pending(tmp_path: Path) -> None:
    db_path = _db(tmp_path)
    with get_connection(db_path) as conn:
        lines = build_status_summary(conn, [_fresh_state()])

    approval_lines = [l for l in lines if "Approvals:" in l]
    assert approval_lines
    assert "0 pending" in approval_lines[0]
