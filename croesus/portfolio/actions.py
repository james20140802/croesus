from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path


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
