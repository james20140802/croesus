"""
List trade proposals awaiting approval (Sprint 011).

Sweeps overdue pending proposals to ``expired`` first, so everything printed
is genuinely decidable. Decide with::

    python -m croesus.jobs.approve_action <action_id> [--reject] [--notes "..."]
"""
from __future__ import annotations

import argparse
from typing import Sequence

from croesus.db.connection import get_connection, resolve_db_path
from croesus.db.migrate import migrate
from croesus.portfolio.approvals import expire_stale_approvals, list_pending_approvals


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.list_pending_approvals",
        description="List rebalance proposals awaiting approval.",
    )
    parser.add_argument("--db-path", default=None, help="override the DuckDB path")
    args = parser.parse_args(argv)

    resolved = resolve_db_path(args.db_path)
    migrate(resolved)
    with get_connection(resolved) as conn:
        expired = expire_stale_approvals(conn)
        pending = list_pending_approvals(conn)

    if expired:
        print(f"{expired} stale proposal(s) transitioned to expired.")
    if not pending:
        print("No proposals awaiting approval.")
        return 0

    print(f"{len(pending)} proposal(s) awaiting approval:\n")
    for p in pending:
        value = f"~${p.estimated_trade_value:,.0f}" if p.estimated_trade_value else "-"
        expires = p.expires_at.strftime("%Y-%m-%d %H:%M UTC") if p.expires_at else "-"
        print(f"  {p.action_id}")
        print(f"    {p.action_type:<10} {p.asset_id or '-':<16} {value:<12} expires {expires}")
        print(f"    {p.human_readable_reason}")
        print()
    print(
        "Decide with: python -m croesus.jobs.approve_action <action_id> "
        '[--reject] [--notes "..."]'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
