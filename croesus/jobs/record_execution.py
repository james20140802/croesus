"""
Record the manual execution of a proposed action (Sprint 006c).

This closes the loop after the rebalancing engine proposes a trade and the user
fills it themselves at a broker: it writes a real ledger transaction and links
it back to the proposed action via ``linked_action_id``. It performs **no**
broker calls and places **no** orders — it only records what the user already
did manually.

    python -m croesus.jobs.record_execution --action-id ACTION --quantity 2 --price 190

The buy/sell direction is inferred from the action's ``action_type`` (a ``trim``
or ``raise_cash`` is a sell, an ``add`` is a buy, a ``rebalance_to_band``
follows whether the proposed weight is above or below current) and can be
overridden with ``--type``.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import (
    RESULT_RECORDED,
    TXN_BUY,
    TXN_SELL,
    PortfolioTransaction,
    TransactionResult,
)

# Outcome statuses (stable; a future UI keys off these).
EXEC_RECORDED = "recorded"
EXEC_REJECTED = "rejected"
EXEC_NOT_FOUND = "not_found"
EXEC_PORTFOLIO_MISMATCH = "portfolio_mismatch"
EXEC_AMBIGUOUS_DIRECTION = "ambiguous_direction"

# action_type -> sell side. ``add`` is a buy; ``rebalance_to_band`` is resolved
# by comparing proposed vs current weight at call time.
_SELL_ACTIONS = frozenset({"trim", "raise_cash"})
_BUY_ACTIONS = frozenset({"add"})


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    action_id: str
    portfolio_id: str | None = None
    transaction: PortfolioTransaction | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == EXEC_RECORDED


def record_execution(
    conn: duckdb.DuckDBPyConnection,
    action_id: str,
    *,
    quantity: float,
    price: float,
    transaction_type: str | None = None,
    fees: float = 0.0,
    transaction_date: date | None = None,
    portfolio_id: str | None = None,
    currency: str | None = None,
    source: str = "manual_execution",
    transaction_id: str | None = None,
) -> ExecutionResult:
    """Record a transaction for the manual fill of a proposed ``action_id``.

    Loads the action (joined to its rebalance run to find the owning portfolio),
    optionally checks it belongs to ``portfolio_id``, infers buy/sell unless
    ``transaction_type`` is given, and records the linked transaction. Returns a
    structured result rather than raising, so a form flow can surface the reason.
    """
    action = _load_action(conn, action_id)
    if action is None:
        return ExecutionResult(
            EXEC_NOT_FOUND, action_id, errors=[f"no proposed action {action_id!r}"]
        )

    owner = action["portfolio_id"]
    if portfolio_id is not None and portfolio_id != owner:
        return ExecutionResult(
            EXEC_PORTFOLIO_MISMATCH,
            action_id,
            portfolio_id=owner,
            errors=[
                f"action {action_id!r} belongs to portfolio {owner!r}, "
                f"not {portfolio_id!r}"
            ],
        )

    ttype = transaction_type or _infer_transaction_type(action)
    if ttype is None:
        return ExecutionResult(
            EXEC_AMBIGUOUS_DIRECTION,
            action_id,
            portfolio_id=owner,
            errors=[
                f"cannot infer buy/sell from action_type "
                f"{action['action_type']!r}; pass --type"
            ],
        )

    txn = PortfolioTransaction(
        transaction_id=transaction_id or f"txn-{uuid.uuid4().hex[:16]}",
        portfolio_id=owner,
        asset_id=action["asset_id"],
        transaction_date=transaction_date or date.today(),
        transaction_type=ttype,
        quantity=quantity,
        price=price,
        currency=currency,
        fees=fees,
        source=source,
        linked_action_id=action_id,
    )
    result: TransactionResult = TransactionRepository(conn).record_transaction(txn)
    if result.status != RESULT_RECORDED:
        return ExecutionResult(
            EXEC_REJECTED, action_id, portfolio_id=owner, errors=result.errors
        )
    return ExecutionResult(
        EXEC_RECORDED, action_id, portfolio_id=owner, transaction=result.transaction
    )


def _load_action(
    conn: duckdb.DuckDBPyConnection, action_id: str
) -> dict[str, Any] | None:
    """Load a proposed action joined to its run's owning portfolio."""
    row = conn.execute(
        """
        SELECT pa.action_id, pa.asset_id, pa.action_type,
               pa.current_weight, pa.proposed_weight, rr.portfolio_id
        FROM proposed_actions pa
        JOIN rebalance_runs rr ON pa.run_id = rr.run_id
        WHERE pa.action_id = ?
        """,
        [action_id],
    ).fetchone()
    if row is None:
        return None
    columns = [desc[0] for desc in conn.description]
    return dict(zip(columns, row))


def _infer_transaction_type(action: dict[str, Any]) -> str | None:
    atype = action["action_type"]
    if atype in _SELL_ACTIONS:
        return TXN_SELL
    if atype in _BUY_ACTIONS:
        return TXN_BUY
    if atype == "rebalance_to_band":
        current = action.get("current_weight")
        proposed = action.get("proposed_weight")
        if current is None or proposed is None or proposed == current:
            return None  # equal weights are a no-op; force an explicit --type
        return TXN_SELL if proposed < current else TXN_BUY
    return None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.record_execution",
        description=(
            "Record the manual execution of a proposed rebalancing action as a "
            "ledger transaction. Performs no broker operations."
        ),
    )
    parser.add_argument("--action-id", required=True, help="proposed action id")
    parser.add_argument(
        "--quantity", required=True, type=float, help="quantity filled"
    )
    parser.add_argument(
        "--price", required=True, type=float, help="fill price per share"
    )
    parser.add_argument(
        "--type",
        dest="transaction_type",
        choices=[TXN_BUY, TXN_SELL],
        help="override inferred buy/sell direction",
    )
    parser.add_argument("--fees", type=float, default=0.0, help="fees paid")
    parser.add_argument("--currency", help="trade currency (default: portfolio base)")
    parser.add_argument(
        "--portfolio-id",
        help="assert the action belongs to this portfolio before recording",
    )
    parser.add_argument(
        "--date",
        dest="transaction_date",
        metavar="YYYY-MM-DD",
        help="execution date (default: today)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    txn_date = None
    if args.transaction_date:
        try:
            txn_date = date.fromisoformat(args.transaction_date)
        except ValueError as exc:
            print(f"invalid --date: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc

    migrate()
    with get_connection() as conn:
        result = record_execution(
            conn,
            args.action_id,
            quantity=args.quantity,
            price=args.price,
            transaction_type=args.transaction_type,
            fees=args.fees,
            transaction_date=txn_date,
            portfolio_id=args.portfolio_id,
            currency=args.currency,
        )

    if result.ok:
        txn = result.transaction
        print(
            f"recorded {txn.transaction_type} {txn.quantity:g} {txn.asset_id} "
            f"@ {txn.price:g} for portfolio {result.portfolio_id} "
            f"(transaction {txn.transaction_id}, action {result.action_id})"
        )
        return
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
