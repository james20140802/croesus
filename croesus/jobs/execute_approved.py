"""
Execute approved rebalance proposals through the paper broker (Sprint 013).

Human-invoked only — this job is deliberately not registered in ``local_sync``
and there is no scheduled path to it. Only actions that are ``approved``,
inside their 7-day window, and not already executed can reach the broker;
fills are recorded into ``portfolio_transactions`` (linked to the action), so
the next ledger-derived snapshot reflects them automatically.

Usage::

    python -m croesus.jobs.execute_approved <action_id> [--dry-run]
    python -m croesus.jobs.execute_approved --all [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from croesus.db.connection import get_connection, resolve_db_path
from croesus.db.migrate import migrate
from croesus.execution.base import ExecutionBlocked, ExecutionFailed
from croesus.execution.execute import (
    execute_all_approved,
    execute_approved_action,
)
from croesus.execution.paper_broker import PaperBroker


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.execute_approved",
        description=(
            "Execute approved, unexpired rebalance proposals via the paper "
            "broker and record fills in the transaction ledger."
        ),
    )
    parser.add_argument(
        "action_id", nargs="?", default=None,
        help="one approved action id (from list_pending_approvals / the report)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="execute every executable approved action for the portfolio",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the orders that would be submitted; record nothing",
    )
    parser.add_argument("--portfolio-id", default="default")
    parser.add_argument("--fee", type=float, default=0.0, help="flat fee per fill")
    parser.add_argument("--db-path", default=None, help="override the DuckDB path")
    args = parser.parse_args(argv)

    if bool(args.action_id) == args.all:
        parser.error("specify exactly one of <action_id> or --all")

    resolved = resolve_db_path(args.db_path)
    migrate(resolved)
    with get_connection(resolved) as conn:
        broker = PaperBroker(conn, flat_fee=args.fee)
        try:
            if args.all:
                result = execute_all_approved(
                    conn,
                    broker=broker,
                    portfolio_id=args.portfolio_id,
                    dry_run=args.dry_run,
                )
            else:
                result = execute_approved_action(
                    conn,
                    args.action_id,
                    broker=broker,
                    portfolio_id=args.portfolio_id,
                    dry_run=args.dry_run,
                )
        except (ExecutionBlocked, ExecutionFailed) as exc:
            print(exc, file=sys.stderr)
            return 1

    if result.fills:
        print(
            f"{len(result.fills)} fill(s) recorded — run portfolio_snapshot to "
            "see the updated book (ledger-derived)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
