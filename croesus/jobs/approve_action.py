"""
Approve or reject one proposed action (Sprint 011).

Records the human decision on a pending proposal. Approving writes a record
only — no order is placed anywhere in this codebase; Sprint 013's paper
broker will be the first (and only) consumer of approvals, and it may act
solely on ``approved`` and unexpired actions.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from croesus.db.connection import get_connection, resolve_db_path
from croesus.db.migrate import migrate
from croesus.portfolio.approvals import (
    ApprovalError,
    approve_action,
    reject_action,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.approve_action",
        description="Approve (default) or reject one pending rebalance proposal.",
    )
    parser.add_argument("action_id", help="id shown by list_pending_approvals")
    parser.add_argument(
        "--reject", action="store_true", help="reject instead of approve"
    )
    parser.add_argument("--notes", default=None, help="free-text decision note")
    parser.add_argument("--db-path", default=None, help="override the DuckDB path")
    args = parser.parse_args(argv)

    resolved = resolve_db_path(args.db_path)
    migrate(resolved)
    with get_connection(resolved) as conn:
        try:
            if args.reject:
                action = reject_action(conn, args.action_id, notes=args.notes)
            else:
                action = approve_action(conn, args.action_id, notes=args.notes)
        except ApprovalError as exc:
            print(exc, file=sys.stderr)
            return 1

    print(
        f"{action.approval_status}: {action.action_id} "
        f"({action.action_type} {action.asset_id or action.sleeve_name or '-'})"
    )
    if args.notes:
        print(f"  notes: {args.notes}")
    if action.approval_status == "approved":
        print(
            "  Recorded only — nothing is executed. Execution (Sprint 013) "
            "will act exclusively on approved, unexpired actions."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
