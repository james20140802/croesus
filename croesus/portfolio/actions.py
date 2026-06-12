from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

# ── Approval gate states (Sprint 011; stable, part of the product contract) ──
# Defined on the model module so both the repository (which stamps the pending
# default on persist) and the approvals workflow can import them without a cycle.
APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_REJECTED = "rejected"
APPROVAL_EXPIRED = "expired"

# How long a proposal stays approvable. The market context behind a proposal
# goes stale; a week forces a fresh rebalance run instead of acting on old data.
APPROVAL_TTL_DAYS = 7


@dataclass(frozen=True)
class ProposedAction:
    action_id: str
    run_id: str
    asset_id: str | None
    sleeve_name: str | None
    action_type: str
    current_weight: float | None
    target_weight: float | None
    proposed_weight: float | None
    estimated_trade_value: float | None
    reason_codes: list[str]
    human_readable_reason: str
    requires_research: bool
    requires_user_approval: bool
    # Sprint 011 approval gate. None on actions that need no approval; the
    # repository stamps 'pending' + a 7-day expiry on persist when
    # requires_user_approval is set. Timestamps are naive-UTC (DuckDB TIMESTAMP).
    approval_status: str | None = None
    approved_at: datetime | None = None
    approval_notes: str | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class RebalanceRunResult:
    run_id: str
    portfolio_id: str
    profile_id: str
    as_of_date: date
    decision: str
    actions: list[ProposedAction]
    markdown_report_path: Path | None
    csv_report_path: Path | None
