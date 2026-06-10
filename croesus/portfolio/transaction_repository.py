"""
Persistence for the transaction ledger (Sprint 006c).

``TransactionRepository`` is the only thing that writes ``portfolio_transactions``.
``record_transaction`` validates first (via :func:`validate_transaction`) and
returns a structured :class:`TransactionResult` instead of raising, so a future
form-submission flow can surface field errors without exception handling.
Reads are ordered ``(transaction_date, transaction_id)`` so derivation is
deterministic and a history view needs no extra sort.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import Any

import duckdb

from croesus.portfolio.transactions import (
    RESULT_RECORDED,
    RESULT_REJECTED,
    PortfolioTransaction,
    TransactionResult,
    effective_gross_amount,
    validate_transaction,
)


class TransactionRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def record_transaction(
        self, transaction: PortfolioTransaction
    ) -> TransactionResult:
        """Validate and persist one transaction.

        Returns a rejected result (no DB write) when validation fails; otherwise
        inserts the row and returns it. ``gross_amount`` is backfilled from
        ``quantity * price`` when the caller left it blank so the stored row is
        self-describing.
        """
        errors = validate_transaction(transaction)
        if errors:
            return TransactionResult(RESULT_REJECTED, None, errors)

        stored = transaction
        if stored.gross_amount is None:
            gross = effective_gross_amount(stored)
            if gross is not None:
                stored = replace(stored, gross_amount=gross)

        self.conn.execute(
            """
            INSERT INTO portfolio_transactions (
              transaction_id, portfolio_id, asset_id, transaction_date,
              transaction_type, quantity, price, gross_amount, currency,
              fees, source, linked_action_id, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)
            """,
            self._to_params(stored),
        )
        return TransactionResult(RESULT_RECORDED, stored, [])

    def list_transactions(
        self,
        portfolio_id: str,
        *,
        asset_id: str | None = None,
        up_to: date | None = None,
    ) -> list[PortfolioTransaction]:
        """All transactions for a portfolio, oldest first (deterministic).

        ``asset_id`` filters to one security; ``up_to`` (inclusive) bounds the
        as-of date so holdings can be derived for a historical date.
        """
        clauses = ["portfolio_id = ?"]
        params: list[Any] = [portfolio_id]
        if asset_id is not None:
            clauses.append("asset_id = ?")
            params.append(asset_id)
        if up_to is not None:
            clauses.append("transaction_date <= ?")
            params.append(up_to)
        rows = self.conn.execute(
            f"""
            SELECT * FROM portfolio_transactions
            WHERE {" AND ".join(clauses)}
            ORDER BY transaction_date, transaction_id
            """,
            params,
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [self._row_to_txn(dict(zip(columns, row))) for row in rows]

    def get_transaction(self, transaction_id: str) -> PortfolioTransaction | None:
        row = self.conn.execute(
            "SELECT * FROM portfolio_transactions WHERE transaction_id = ?",
            [transaction_id],
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self.conn.description]
        return self._row_to_txn(dict(zip(columns, row)))

    def transactions_for_action(
        self, linked_action_id: str
    ) -> list[PortfolioTransaction]:
        rows = self.conn.execute(
            """
            SELECT * FROM portfolio_transactions
            WHERE linked_action_id = ?
            ORDER BY transaction_date, transaction_id
            """,
            [linked_action_id],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [self._row_to_txn(dict(zip(columns, row))) for row in rows]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _to_params(txn: PortfolioTransaction) -> tuple[Any, ...]:
        return (
            txn.transaction_id,
            txn.portfolio_id,
            txn.asset_id,
            txn.transaction_date,
            txn.transaction_type,
            txn.quantity,
            txn.price,
            txn.gross_amount,
            txn.currency,
            txn.fees,
            txn.source,
            txn.linked_action_id,
            json.dumps(txn.metadata),
        )

    @staticmethod
    def _row_to_txn(row: dict[str, Any]) -> PortfolioTransaction:
        return PortfolioTransaction(
            transaction_id=row["transaction_id"],
            portfolio_id=row["portfolio_id"],
            asset_id=row["asset_id"],
            transaction_date=row["transaction_date"],
            transaction_type=row["transaction_type"],
            quantity=row["quantity"],
            price=row["price"],
            gross_amount=row["gross_amount"],
            currency=row["currency"],
            fees=row["fees"],
            source=row["source"],
            linked_action_id=row["linked_action_id"],
            metadata=_to_dict(row.get("metadata")),
        )


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value or {}
