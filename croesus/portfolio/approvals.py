"""
Approval gate for proposed actions (Sprint 011).

Every trade proposal (``requires_user_approval``) is persisted as ``pending``
with a 7-day expiry. The functions here are the *only* writers of approval
state:

  - ``expire_stale_approvals`` — deterministic pending → expired sweep, run
    before any read or decision so a stale proposal can never be approved.
  - ``approve_action`` / ``reject_action`` — record the human's decision once;
    a decided or expired action cannot be re-decided.
  - ``list_pending_approvals`` — what currently awaits the user.

Approving an action only writes a record. No execution path exists in this
codebase yet (Sprint 013 adds a paper broker that may act *only* on
approved, unexpired actions).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import duckdb

from croesus.portfolio.actions import (
    APPROVAL_APPROVED,
    APPROVAL_EXPIRED,
    APPROVAL_PENDING,
    APPROVAL_REJECTED,
    APPROVAL_TTL_DAYS,
    ProposedAction,
)
from croesus.portfolio.repository import PortfolioRepository

__all__ = [
    "APPROVAL_APPROVED",
    "APPROVAL_EXPIRED",
    "APPROVAL_PENDING",
    "APPROVAL_REJECTED",
    "APPROVAL_TTL_DAYS",
    "ApprovalError",
    "PendingApproval",
    "approve_action",
    "default_expiry",
    "expire_stale_approvals",
    "list_pending_approvals",
    "naive_utc_now",
    "reject_action",
]


class ApprovalError(ValueError):
    """The requested approval transition is not allowed."""


@dataclass(frozen=True)
class PendingApproval:
    """One action awaiting a decision, with just the fields the CLI shows."""

    action_id: str
    run_id: str
    asset_id: str | None
    action_type: str
    estimated_trade_value: float | None
    human_readable_reason: str
    expires_at: datetime | None


def naive_utc_now(now: datetime | None = None) -> datetime:
    """Normalise to naive-UTC, matching how DuckDB TIMESTAMPs are stored."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    return now


def default_expiry(now: datetime | None = None) -> datetime:
    return naive_utc_now(now) + timedelta(days=APPROVAL_TTL_DAYS)


def expire_stale_approvals(
    conn: duckdb.DuckDBPyConnection, *, now: datetime | None = None
) -> int:
    """Transition every overdue pending action to expired. Idempotent."""
    cutoff = naive_utc_now(now)
    before = conn.execute(
        "SELECT COUNT(*) FROM proposed_actions "
        "WHERE approval_status = ? AND expires_at IS NOT NULL AND expires_at <= ?",
        [APPROVAL_PENDING, cutoff],
    ).fetchone()[0]
    if before:
        conn.execute(
            "UPDATE proposed_actions SET approval_status = ? "
            "WHERE approval_status = ? AND expires_at IS NOT NULL AND expires_at <= ?",
            [APPROVAL_EXPIRED, APPROVAL_PENDING, cutoff],
        )
    return int(before)


def list_pending_approvals(
    conn: duckdb.DuckDBPyConnection, *, now: datetime | None = None
) -> list[PendingApproval]:
    """Sweep expiry, then return every action still awaiting a decision."""
    expire_stale_approvals(conn, now=now)
    rows = conn.execute(
        """
        SELECT action_id, run_id, asset_id, action_type,
               estimated_trade_value, human_readable_reason, expires_at
        FROM proposed_actions
        WHERE approval_status = ?
        ORDER BY expires_at NULLS LAST, action_id
        """,
        [APPROVAL_PENDING],
    ).fetchall()
    return [PendingApproval(*row) for row in rows]


def approve_action(
    conn: duckdb.DuckDBPyConnection,
    action_id: str,
    *,
    notes: str | None = None,
    now: datetime | None = None,
) -> ProposedAction:
    return _decide(conn, action_id, APPROVAL_APPROVED, notes=notes, now=now)


def reject_action(
    conn: duckdb.DuckDBPyConnection,
    action_id: str,
    *,
    notes: str | None = None,
    now: datetime | None = None,
) -> ProposedAction:
    return _decide(conn, action_id, APPROVAL_REJECTED, notes=notes, now=now)


def _decide(
    conn: duckdb.DuckDBPyConnection,
    action_id: str,
    new_status: str,
    *,
    notes: str | None,
    now: datetime | None,
) -> ProposedAction:
    # Sweep first so a proposal past its window can never be decided.
    expire_stale_approvals(conn, now=now)

    row = conn.execute(
        "SELECT approval_status, run_id FROM proposed_actions WHERE action_id = ?",
        [action_id],
    ).fetchone()
    if row is None:
        raise ApprovalError(f"action not found: {action_id}")
    status, run_id = row
    if status is None:
        raise ApprovalError(
            f"action {action_id} does not require approval (no approval record)"
        )
    if status == APPROVAL_EXPIRED:
        raise ApprovalError(
            f"action {action_id} expired — run a fresh rebalance_check and "
            "decide on the new proposal"
        )
    if status != APPROVAL_PENDING:
        raise ApprovalError(f"action {action_id} is already {status}")

    conn.execute(
        """
        UPDATE proposed_actions
        SET approval_status = ?, approved_at = ?, approval_notes = ?
        WHERE action_id = ? AND approval_status = ?
        """,
        [new_status, naive_utc_now(now), notes, action_id, APPROVAL_PENDING],
    )
    updated = next(
        a
        for a in PortfolioRepository(conn).list_proposed_actions(run_id)
        if a.action_id == action_id
    )
    return updated
