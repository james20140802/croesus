"""
Transaction ledger model and validation (Sprint 006c).

This module is the pure, DB-free core of the ledger: the
:class:`PortfolioTransaction` value object, the stable set of transaction
types, and :func:`validate_transaction` which decides whether a transaction is
well-formed *before* it is persisted. Keeping validation here (not in the
repository or the CLI) means a future local web form can reuse the exact same
rules and error strings the CLI uses.

P&L semantics (documented once, applied by ``holdings_from_transactions``):

  - ``cost_basis`` on a derived holding is the base-currency total *open* cost
    of the position, including buy-side fees.
  - realized P&L comes from ``sell`` transactions (proceeds minus the average
    cost of the shares sold, minus sell-side fees).
  - unrealized P&L is *not* computed here — it comes from mark-to-market against
    live prices, exactly as the snapshot pipeline already does.
  - ``dividend`` is income: it adds cash, it does not reduce cost basis.

Tax-lot accounting is out of scope; average cost is used.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

# ── Transaction types (stable product contract — used by future UI forms) ────
TXN_BUY = "buy"
TXN_SELL = "sell"
TXN_DEPOSIT = "deposit"
TXN_WITHDRAWAL = "withdrawal"
TXN_DIVIDEND = "dividend"
TXN_FEE = "fee"
TXN_MANUAL_ADJUSTMENT = "manual_adjustment"

TRANSACTION_TYPES: frozenset[str] = frozenset(
    {
        TXN_BUY,
        TXN_SELL,
        TXN_DEPOSIT,
        TXN_WITHDRAWAL,
        TXN_DIVIDEND,
        TXN_FEE,
        TXN_MANUAL_ADJUSTMENT,
    }
)

# Types that move a specific security position and therefore require an asset.
_SECURITY_TYPES: frozenset[str] = frozenset({TXN_BUY, TXN_SELL, TXN_MANUAL_ADJUSTMENT})

# Status values for a recording attempt (stable; a form flow keys off these).
RESULT_RECORDED = "recorded"
RESULT_REJECTED = "rejected"


@dataclass(frozen=True)
class PortfolioTransaction:
    """One ledger event. Mirrors the ``portfolio_transactions`` table."""

    transaction_id: str
    portfolio_id: str
    transaction_date: date
    transaction_type: str
    asset_id: str | None = None
    quantity: float | None = None
    price: float | None = None
    gross_amount: float | None = None
    currency: str | None = None
    fees: float | None = None
    source: str | None = None
    linked_action_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransactionResult:
    """Structured outcome of a recording attempt, suitable for a form flow."""

    status: str
    transaction: PortfolioTransaction | None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == RESULT_RECORDED


def effective_gross_amount(txn: PortfolioTransaction) -> float | None:
    """Gross cash amount for a transaction.

    For buys/sells this is ``quantity * price`` when not stated explicitly; for
    cash events (deposit/withdrawal/dividend/fee) the caller supplies it. Fees
    are *not* folded in here — cash impact handles them separately so the gross
    notional stays a clean ``qty * price``.
    """
    if txn.gross_amount is not None:
        return txn.gross_amount
    if txn.quantity is not None and txn.price is not None:
        return txn.quantity * txn.price
    return None


def validate_transaction(txn: PortfolioTransaction) -> list[str]:
    """Return a list of human-readable validation errors (empty == valid).

    Rules are intentionally permissive enough for a local MVP but strict enough
    that a derived-holdings pass cannot silently corrupt a position:

    - the type must be one of :data:`TRANSACTION_TYPES`;
    - buy/sell need an asset, a positive quantity, and a non-negative price;
    - deposit/withdrawal/dividend need a positive gross amount;
    - fee needs a positive ``fees`` or ``gross_amount``;
    - manual_adjustment needs an asset and a (possibly negative, non-zero)
      quantity;
    - fees, when present, may not be negative.
    """
    errors: list[str] = []
    ttype = txn.transaction_type

    if ttype not in TRANSACTION_TYPES:
        errors.append(
            f"unknown transaction_type {ttype!r}; "
            f"expected one of {sorted(TRANSACTION_TYPES)}"
        )
        return errors  # nothing else is meaningful without a known type

    if not txn.portfolio_id:
        errors.append("portfolio_id is required")

    if txn.fees is not None and txn.fees < 0:
        errors.append("fees may not be negative")

    if ttype in (TXN_BUY, TXN_SELL):
        if not txn.asset_id:
            errors.append(f"{ttype} requires an asset_id")
        if txn.quantity is None or txn.quantity <= 0:
            errors.append(f"{ttype} requires a positive quantity")
        if txn.price is None or txn.price < 0:
            errors.append(f"{ttype} requires a non-negative price")
    elif ttype in (TXN_DEPOSIT, TXN_WITHDRAWAL, TXN_DIVIDEND):
        if txn.gross_amount is None or txn.gross_amount <= 0:
            errors.append(f"{ttype} requires a positive gross_amount")
    elif ttype == TXN_FEE:
        fee = txn.fees if txn.fees is not None else txn.gross_amount
        if fee is None or fee <= 0:
            errors.append("fee requires a positive fees or gross_amount")
    elif ttype == TXN_MANUAL_ADJUSTMENT:
        if not txn.asset_id:
            errors.append("manual_adjustment requires an asset_id")
        if txn.quantity is None or txn.quantity == 0:
            errors.append("manual_adjustment requires a non-zero quantity")

    return errors


def is_security_type(transaction_type: str) -> bool:
    """True when the type moves a named security position (needs an asset)."""
    return transaction_type in _SECURITY_TYPES
