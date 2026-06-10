"""
Record a manual, self-directed ledger transaction (Sprint 006d follow-up).

The companion :mod:`croesus.jobs.record_execution` records the fill of a
*proposed* action. This job covers the other half: a transaction the user
initiated on their own — a buy or sell the system never proposed, a deposit, a
withdrawal, a dividend, a fee, or a manual adjustment. It writes one append-only
ledger row and performs **no** broker calls and places **no** orders.

    python -m croesus.jobs.record_transaction \\
        --type buy --asset VOO --quantity 2 --price 670 --date 2026-06-10

A ``--asset`` symbol is resolved to an ``asset_id`` against the existing assets
table (no network); pass ``--asset-id`` to skip resolution. Cash-only events
(deposit/withdrawal/dividend/fee) take ``--amount`` instead of quantity/price.
The transaction is validated before it is written and a structured result is
returned, so a future local form can surface the same field errors the CLI does.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

import duckdb

from croesus.assets.resolver import AssetResolver
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.repository import PortfolioRepository
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import (
    RESULT_RECORDED,
    TRANSACTION_TYPES,
    PortfolioTransaction,
    TransactionResult,
    is_security_type,
)

# Outcome statuses (stable; a future UI keys off these).
RECORD_RECORDED = "recorded"
RECORD_REJECTED = "rejected"
RECORD_UNRESOLVED_SYMBOL = "unresolved_symbol"

_DEFAULT_PORTFOLIO_ID = "default"


@dataclass(frozen=True)
class RecordTransactionResult:
    status: str
    transaction: PortfolioTransaction | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == RECORD_RECORDED


def record_manual_transaction(
    conn: duckdb.DuckDBPyConnection,
    *,
    transaction_type: str,
    symbol: str | None = None,
    asset_id: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    gross_amount: float | None = None,
    fees: float | None = None,
    currency: str | None = None,
    transaction_date: date | None = None,
    portfolio_id: str = _DEFAULT_PORTFOLIO_ID,
    source: str = "manual",
    transaction_id: str | None = None,
    note: str | None = None,
) -> RecordTransactionResult:
    """Validate and record one self-directed ledger transaction.

    Resolves ``symbol`` to an ``asset_id`` (DB-only) when ``asset_id`` is not
    given directly. Defaults the currency to the portfolio's base. Returns a
    structured result instead of raising so a form flow can surface the reason;
    nothing is written unless validation passes.
    """
    resolved_asset_id = asset_id
    if resolved_asset_id is None and symbol:
        # No metadata/price providers -> pure DB lookup, no network. An unknown
        # symbol is reported (not silently created) so a typo cannot mint a
        # bogus position; record a snapshot first or pass --asset-id.
        resolution = AssetResolver(conn).resolve_symbol(symbol)
        if resolution.asset_id is None:
            return RecordTransactionResult(
                RECORD_UNRESOLVED_SYMBOL,
                errors=[
                    f"symbol {symbol!r} not found in assets "
                    f"({resolution.message or 'unresolved'}); "
                    "run a snapshot first or pass --asset-id"
                ],
            )
        resolved_asset_id = resolution.asset_id

    if currency is None:
        currency = _portfolio_base_currency(conn, portfolio_id)

    metadata: dict[str, Any] = {"note": note} if note else {}

    txn = PortfolioTransaction(
        transaction_id=transaction_id or f"txn-{uuid.uuid4().hex[:16]}",
        portfolio_id=portfolio_id,
        asset_id=resolved_asset_id,
        transaction_date=transaction_date or date.today(),
        transaction_type=transaction_type,
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        currency=currency,
        fees=fees,
        source=source,
        metadata=metadata,
    )
    result: TransactionResult = TransactionRepository(conn).record_transaction(txn)
    if result.status != RESULT_RECORDED:
        return RecordTransactionResult(RECORD_REJECTED, errors=result.errors)
    return RecordTransactionResult(RECORD_RECORDED, transaction=result.transaction)


def _portfolio_base_currency(
    conn: duckdb.DuckDBPyConnection, portfolio_id: str
) -> str:
    portfolio = PortfolioRepository(conn).get_portfolio(portfolio_id)
    if portfolio is not None and portfolio.base_currency:
        return portfolio.base_currency
    return "USD"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m croesus.jobs.record_transaction",
        description=(
            "Record a self-directed ledger transaction (a buy/sell the system "
            "did not propose, or a deposit/withdrawal/dividend/fee/adjustment). "
            "Performs no broker operations."
        ),
    )
    parser.add_argument(
        "--type",
        dest="transaction_type",
        required=True,
        choices=sorted(TRANSACTION_TYPES),
        help="transaction type",
    )
    parser.add_argument("--asset", dest="symbol", help="ticker symbol (resolved to asset_id)")
    parser.add_argument("--asset-id", help="explicit asset_id (skips symbol resolution)")
    parser.add_argument("--quantity", type=float, help="quantity (buy/sell/adjustment)")
    parser.add_argument("--price", type=float, help="price per share (buy/sell)")
    parser.add_argument(
        "--amount",
        dest="gross_amount",
        type=float,
        help="gross cash amount (deposit/withdrawal/dividend/fee)",
    )
    parser.add_argument("--fees", type=float, help="fees paid")
    parser.add_argument("--currency", help="trade currency (default: portfolio base)")
    parser.add_argument(
        "--portfolio-id",
        default=_DEFAULT_PORTFOLIO_ID,
        help="portfolio to record against (default: %(default)s)",
    )
    parser.add_argument(
        "--date",
        dest="transaction_date",
        metavar="YYYY-MM-DD",
        help="transaction date (default: today)",
    )
    parser.add_argument("--note", help="free-text note stored in metadata")
    parser.add_argument("--id", dest="transaction_id", help="explicit transaction id")
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
        result = record_manual_transaction(
            conn,
            transaction_type=args.transaction_type,
            symbol=args.symbol,
            asset_id=args.asset_id,
            quantity=args.quantity,
            price=args.price,
            gross_amount=args.gross_amount,
            fees=args.fees,
            currency=args.currency,
            transaction_date=txn_date,
            portfolio_id=args.portfolio_id,
            transaction_id=args.transaction_id,
            note=args.note,
        )

    if result.ok:
        txn = result.transaction
        target = txn.asset_id or "cash"
        detail = (
            f"{txn.quantity:g} @ {txn.price:g}"
            if is_security_type(txn.transaction_type) and txn.price is not None
            else f"amount {txn.gross_amount:g}"
            if txn.gross_amount is not None
            else ""
        )
        print(
            f"recorded {txn.transaction_type} {target} {detail} "
            f"for portfolio {txn.portfolio_id} (transaction {txn.transaction_id})"
        )
        return
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    main()
