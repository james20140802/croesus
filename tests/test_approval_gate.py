"""Sprint 011: approval gate — pending default, expiry, decisions, report."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.approve_action import main as approve_main
from croesus.jobs.list_pending_approvals import main as list_main
from croesus.portfolio.actions import ProposedAction
from croesus.portfolio.approvals import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    ApprovalError,
    approve_action,
    expire_stale_approvals,
    list_pending_approvals,
    reject_action,
)
from croesus.portfolio.repository import PortfolioRepository
from croesus.reports.portfolio_action import write_portfolio_action_reports

AS_OF = date(2026, 6, 1)
NOW = datetime(2026, 6, 1, 12, 0, 0)


def _action(
    action_id: str = "run-1-001",
    run_id: str = "run-1",
    *,
    action_type: str = "trim",
    requires_user_approval: bool = True,
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        run_id=run_id,
        asset_id="US_EQ_NVDA",
        sleeve_name="satellite_equity",
        action_type=action_type,
        current_weight=0.18,
        target_weight=0.10,
        proposed_weight=0.10,
        estimated_trade_value=8000.0,
        reason_codes=["POSITION_OVER_MAX"],
        human_readable_reason="Trim US_EQ_NVDA from 18.0% to 10.0%.",
        requires_research=False,
        requires_user_approval=requires_user_approval,
    )


def _open(tmp_path: Path):
    db_path = tmp_path / "a.duckdb"
    migrate(db_path)
    return get_connection(db_path)


def _persist_run(conn, run_id: str, actions, *, now: datetime = NOW) -> None:
    repo = PortfolioRepository(conn)
    repo.upsert_rebalance_run(
        run_id, "default", "default", AS_OF,
        decision="rebalance_recommended", summary="test", metadata={},
    )
    repo.replace_proposed_actions(run_id, actions, now=now)


# ── pending default + expiry stamp ───────────────────────────────────────────

def test_persisting_approvable_action_stamps_pending_and_expiry(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _persist_run(conn, "run-1", [_action()])
        stored = PortfolioRepository(conn).list_proposed_actions("run-1")[0]

    assert stored.approval_status == APPROVAL_PENDING
    assert stored.expires_at == NOW + timedelta(days=7)
    assert stored.approved_at is None


def test_non_approvable_action_gets_no_approval_record(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _persist_run(
            conn, "run-1",
            [_action(action_type="watch", requires_user_approval=False)],
        )
        stored = PortfolioRepository(conn).list_proposed_actions("run-1")[0]

    assert stored.approval_status is None
    assert stored.expires_at is None


# ── decisions ─────────────────────────────────────────────────────────────────

def test_approve_and_reject_record_decision_once(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _persist_run(
            conn, "run-1", [_action("run-1-001"), _action("run-1-002")]
        )
        approved = approve_action(conn, "run-1-001", notes="ok by me", now=NOW)
        rejected = reject_action(conn, "run-1-002", now=NOW)

        assert approved.approval_status == APPROVAL_APPROVED
        assert approved.approved_at == NOW
        assert approved.approval_notes == "ok by me"
        assert rejected.approval_status == APPROVAL_REJECTED

        # A decided action cannot be re-decided in either direction.
        with pytest.raises(ApprovalError, match="already approved"):
            approve_action(conn, "run-1-001", now=NOW)
        with pytest.raises(ApprovalError, match="already rejected"):
            approve_action(conn, "run-1-002", now=NOW)


def test_unknown_and_non_approvable_actions_raise(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _persist_run(
            conn, "run-1",
            [_action(action_type="watch", requires_user_approval=False)],
        )
        with pytest.raises(ApprovalError, match="not found"):
            approve_action(conn, "nope", now=NOW)
        with pytest.raises(ApprovalError, match="does not require approval"):
            approve_action(conn, "run-1-001", now=NOW)


# ── expiry ────────────────────────────────────────────────────────────────────

def test_pending_transitions_to_expired_after_seven_days(tmp_path) -> None:
    after_expiry = NOW + timedelta(days=7, seconds=1)
    with _open(tmp_path) as conn:
        _persist_run(conn, "run-1", [_action()])

        assert list_pending_approvals(conn, now=NOW)  # still pending in window
        assert expire_stale_approvals(conn, now=after_expiry) == 1
        assert expire_stale_approvals(conn, now=after_expiry) == 0  # idempotent
        stored = PortfolioRepository(conn).list_proposed_actions("run-1")[0]
        assert stored.approval_status == APPROVAL_EXPIRED

        with pytest.raises(ApprovalError, match="expired"):
            approve_action(conn, "run-1-001", now=after_expiry)


def test_approving_past_window_expires_instead_of_approving(tmp_path) -> None:
    # Even without a prior sweep, a late approval attempt must not succeed:
    # _decide sweeps first.
    with _open(tmp_path) as conn:
        _persist_run(conn, "run-1", [_action()])
        with pytest.raises(ApprovalError, match="expired"):
            approve_action(conn, "run-1-001", now=NOW + timedelta(days=8))


# ── re-runs never clobber earlier decisions ───────────────────────────────────

def test_new_rebalance_run_does_not_overwrite_earlier_approval(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _persist_run(conn, "run-1", [_action("run-1-001", "run-1")])
        approve_action(conn, "run-1-001", now=NOW)

        # Next day's run proposes the equivalent trade under a new run_id.
        _persist_run(
            conn, "run-2", [_action("run-2-001", "run-2")],
            now=NOW + timedelta(days=1),
        )

        repo = PortfolioRepository(conn)
        old = repo.list_proposed_actions("run-1")[0]
        new = repo.list_proposed_actions("run-2")[0]

    assert old.approval_status == APPROVAL_APPROVED  # untouched
    assert new.approval_status == APPROVAL_PENDING   # decided separately


def test_replacing_same_run_preserves_carried_approval_state(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _persist_run(conn, "run-1", [_action()])
        approved = approve_action(conn, "run-1-001", notes="keep", now=NOW)
        # Re-persisting the run with the already-decided action (round-trip)
        # must not silently reset the decision to pending.
        PortfolioRepository(conn).replace_proposed_actions("run-1", [approved], now=NOW)
        stored = PortfolioRepository(conn).list_proposed_actions("run-1")[0]

    assert stored.approval_status == APPROVAL_APPROVED
    assert stored.approval_notes == "keep"


# ── CLI + report ──────────────────────────────────────────────────────────────

def test_cli_list_and_approve_workflow(tmp_path, capsys, monkeypatch) -> None:
    db_path = tmp_path / "a.duckdb"
    migrate(db_path)
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))
    with get_connection(db_path) as conn:
        _persist_run(conn, "run-1", [_action()], now=datetime.utcnow())

    assert list_main([]) == 0
    out = capsys.readouterr().out
    assert "run-1-001" in out and "awaiting approval" in out

    assert approve_main(["run-1-001", "--notes", "looks fine"]) == 0
    out = capsys.readouterr().out
    assert "approved: run-1-001" in out
    assert "nothing is executed" in out.lower()

    assert approve_main(["run-1-001"]) == 1  # already decided → error exit
    assert "already approved" in capsys.readouterr().err

    assert list_main([]) == 0
    assert "No proposals awaiting approval" in capsys.readouterr().out


def test_report_shows_approval_status_and_expiry(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _persist_run(conn, "run-1", [_action()])
        markdown_path, csv_path = write_portfolio_action_reports(
            conn, "run-1", reports_dir=tmp_path
        )

    markdown = markdown_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "approval: pending" in markdown
    assert "expires 2026-06-08" in markdown
    assert "Nothing executes without an explicit approval." in markdown
    assert "approval_status" in csv_text and "pending" in csv_text
