"""
Post-approval execution (Sprint 013).

This module is the ONLY place in the codebase that programmatically creates
trade transactions, and it acts exclusively on proposals that passed the
approval gate:

  approved AND inside the 7-day window AND not already executed

Everything else raises :class:`ExecutionBlocked` with the reason. Sleeve-level
proposals (no ``asset_id``) are reported as skipped — they describe a target,
not an order, and need a human to pick the instruments.

Fills are recorded into ``portfolio_transactions`` with
``linked_action_id`` = the approved action, so execution is idempotent (a
second attempt sees the existing transaction and blocks) and the next
ledger-derived snapshot reflects the trade automatically (Sprint 009).

This job is deliberately NOT registered in ``local_sync`` — execution is a
human-invoked step, never scheduled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from uuid import uuid4

import duckdb

from croesus.execution.base import (
    SIDE_BUY,
    SIDE_SELL,
    BrokerAdapter,
    ExecutionBlocked,
    ExecutionFailed,
    Fill,
    OrderRequest,
)
from croesus.portfolio.actions import APPROVAL_APPROVED, ProposedAction
from croesus.portfolio.approvals import expire_stale_approvals, naive_utc_now
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import TXN_BUY, TXN_SELL, PortfolioTransaction

# Which proposal types translate into a single-instrument order.
_ACTION_SIDES = {
    "trim": SIDE_SELL,
    "add": SIDE_BUY,
}


@dataclass(frozen=True)
class ExecutionResult:
    portfolio_id: str
    dry_run: bool
    fills: list[Fill] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # action_id -> reason
    planned: list[OrderRequest] = field(default_factory=list)  # dry-run only


def execute_approved_action(
    conn: duckdb.DuckDBPyConnection,
    action_id: str,
    *,
    broker: BrokerAdapter,
    portfolio_id: str = "default",
    dry_run: bool = False,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> ExecutionResult:
    """Execute one approved action (or plan it under ``dry_run``).

    Raises :class:`ExecutionBlocked` when the action is not in an executable
    approval state, and :class:`ExecutionFailed` when the broker cannot fill.
    """
    # Sweep first so an approval whose window lapsed can never reach a broker.
    expire_stale_approvals(conn, now=now)

    action = _load_action(conn, action_id)
    _require_executable_approval(action, now=now)

    if TransactionRepository(conn).transactions_for_action(action_id):
        raise ExecutionBlocked(
            f"action {action_id} was already executed — the ledger has a "
            "transaction linked to it"
        )

    order = _order_for(action, portfolio_id, now)
    if order is None:
        reason = _non_order_reason(action)
        log(f"skipped {action_id}: {reason}")
        return ExecutionResult(
            portfolio_id=portfolio_id, dry_run=dry_run, skipped={action_id: reason}
        )

    if dry_run:
        log(
            f"DRY RUN — would submit: {order.side} {order.asset_id} "
            f"~${order.notional:,.2f} via {broker.venue_name}"
        )
        return ExecutionResult(
            portfolio_id=portfolio_id, dry_run=True, planned=[order]
        )

    fill = broker.submit(order)
    _record_fill(conn, fill, portfolio_id)
    log(
        f"filled {fill.side} {fill.quantity:.4f} {fill.asset_id} @ {fill.price:.2f} "
        f"(fees {fill.fees:.2f}, venue {fill.venue}) — recorded in the ledger"
    )
    return ExecutionResult(portfolio_id=portfolio_id, dry_run=False, fills=[fill])


def list_executable_action_ids(
    conn: duckdb.DuckDBPyConnection,
    *,
    portfolio_id: str = "default",
    now: datetime | None = None,
) -> list[str]:
    """Approved, unexpired, not-yet-executed actions for ``--all``."""
    expire_stale_approvals(conn, now=now)
    cutoff = naive_utc_now(now)
    rows = conn.execute(
        """
        SELECT a.action_id
        FROM proposed_actions a
        JOIN rebalance_runs r ON r.run_id = a.run_id
        WHERE r.portfolio_id = ?
          AND a.approval_status = ?
          AND (a.expires_at IS NULL OR a.expires_at > ?)
          AND NOT EXISTS (
                SELECT 1 FROM portfolio_transactions t
                WHERE t.linked_action_id = a.action_id
              )
        ORDER BY a.action_id
        """,
        [portfolio_id, APPROVAL_APPROVED, cutoff],
    ).fetchall()
    return [row[0] for row in rows]


def _load_action(conn: duckdb.DuckDBPyConnection, action_id: str) -> ProposedAction:
    row = conn.execute(
        "SELECT run_id FROM proposed_actions WHERE action_id = ?", [action_id]
    ).fetchone()
    if row is None:
        raise ExecutionBlocked(f"action not found: {action_id}")
    from croesus.portfolio.repository import PortfolioRepository

    return next(
        a
        for a in PortfolioRepository(conn).list_proposed_actions(row[0])
        if a.action_id == action_id
    )


def _require_executable_approval(
    action: ProposedAction, *, now: datetime | None
) -> None:
    status = action.approval_status
    if status is None:
        raise ExecutionBlocked(
            f"action {action.action_id} carries no approval record "
            f"({action.action_type}) — it is not an executable trade proposal"
        )
    if status != APPROVAL_APPROVED:
        hint = (
            "approve it first with `python -m croesus.jobs.approve_action`"
            if status == "pending"
            else "run a fresh rebalance_check and decide on the new proposal"
        )
        raise ExecutionBlocked(
            f"action {action.action_id} is {status}, not approved — {hint}"
        )
    if action.expires_at is not None and naive_utc_now(now) > action.expires_at:
        # Approved but sat past its window: the market context behind the
        # proposal is stale, so execution is refused as well.
        raise ExecutionBlocked(
            f"action {action.action_id} was approved but its window expired on "
            f"{action.expires_at:%Y-%m-%d} — run a fresh rebalance_check"
        )


def _order_for(
    action: ProposedAction, portfolio_id: str, now: datetime | None
) -> OrderRequest | None:
    side = _ACTION_SIDES.get(action.action_type)
    if side is None or not action.asset_id:
        return None
    notional = action.estimated_trade_value
    if notional is None or notional <= 0:
        return None
    return OrderRequest(
        action_id=action.action_id,
        portfolio_id=portfolio_id,
        asset_id=action.asset_id,
        side=side,
        notional=abs(notional),
        as_of_date=naive_utc_now(now).date(),
    )


def _non_order_reason(action: ProposedAction) -> str:
    if action.action_type not in _ACTION_SIDES:
        return (
            f"{action.action_type} proposals describe a target, not an order — "
            "break it down into instrument-level trades and record them with "
            "record_transaction"
        )
    if not action.asset_id:
        return "no asset_id on the proposal; pick instruments manually"
    return "no positive estimated trade value on the proposal"


def _record_fill(
    conn: duckdb.DuckDBPyConnection, fill: Fill, portfolio_id: str
) -> None:
    currency = _asset_currency(conn, fill.asset_id)
    result = TransactionRepository(conn).record_transaction(
        PortfolioTransaction(
            transaction_id=f"txn-exec-{uuid4().hex[:12]}",
            portfolio_id=portfolio_id,
            transaction_date=fill.fill_date,
            transaction_type=TXN_BUY if fill.side == SIDE_BUY else TXN_SELL,
            asset_id=fill.asset_id,
            quantity=fill.quantity,
            price=fill.price,
            currency=currency,
            fees=fill.fees,
            source=f"{fill.venue}_broker",
            linked_action_id=fill.action_id,
        )
    )
    if result.status != "recorded":
        raise ExecutionFailed(
            f"fill for {fill.action_id} was rejected by ledger validation: "
            f"{'; '.join(result.errors)}"
        )


def _asset_currency(conn: duckdb.DuckDBPyConnection, asset_id: str) -> str:
    row = conn.execute(
        "SELECT currency FROM assets WHERE asset_id = ?", [asset_id]
    ).fetchone()
    return (row[0] or "USD") if row else "USD"


def _summarize(result: ExecutionResult) -> str:
    parts = [f"fills={len(result.fills)}", f"skipped={len(result.skipped)}"]
    if result.dry_run:
        parts.append(f"planned={len(result.planned)} (dry run)")
    return " ".join(parts)


def execute_all_approved(
    conn: duckdb.DuckDBPyConnection,
    *,
    broker: BrokerAdapter,
    portfolio_id: str = "default",
    dry_run: bool = False,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> ExecutionResult:
    """Execute every executable approved action; failures isolate per action."""
    fills: list[Fill] = []
    skipped: dict[str, str] = {}
    planned: list[OrderRequest] = []
    for action_id in list_executable_action_ids(
        conn, portfolio_id=portfolio_id, now=now
    ):
        try:
            result = execute_approved_action(
                conn,
                action_id,
                broker=broker,
                portfolio_id=portfolio_id,
                dry_run=dry_run,
                now=now,
                log=log,
            )
        except (ExecutionBlocked, ExecutionFailed) as exc:
            skipped[action_id] = str(exc)
            log(f"skipped {action_id}: {exc}")
            continue
        fills.extend(result.fills)
        planned.extend(result.planned)
        skipped.update(result.skipped)
    result = ExecutionResult(
        portfolio_id=portfolio_id,
        dry_run=dry_run,
        fills=fills,
        skipped=skipped,
        planned=planned,
    )
    log(f"execution summary: {_summarize(result)}")
    return result
