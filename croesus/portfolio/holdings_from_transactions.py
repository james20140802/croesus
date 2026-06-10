"""
Derive holdings from the transaction ledger (Sprint 006c).

:func:`derive_holdings_from_transactions` is a pure, deterministic fold over a
portfolio's transactions that produces the same ``Holding`` rows the snapshot
pipeline persists — quantity, average cost, and base-currency cost basis — plus
a running cash balance and realized P&L. It is the transaction-sourced
counterpart to ``import_holdings`` (CSV); both feed the *same* mark-to-market
and exposure math downstream.

Accounting model (average cost, no tax lots — see ``transactions`` docstring):

  - ``buy``    : add shares; cost basis grows by ``qty * price + fees``; cash falls.
  - ``sell``   : remove shares at the running average cost; realized P&L is
                 ``(price - avg_cost) * qty - fees``; cash rises by net proceeds.
  - ``dividend``: cash income; does **not** change cost basis or quantity.
  - ``fee``    : cash out; capitalized into a position's cost basis only when the
                 fee names an ``asset_id``, otherwise a pure cash cost.
  - ``deposit`` / ``withdrawal`` : cash in / out, no security effect.
  - ``manual_adjustment`` : correct a quantity directly (e.g. a split or a
                 reconciliation); cash is untouched.

Long-only MVP: a sell larger than the held quantity is clamped to the position
and a warning is emitted (no short positions). Amounts are treated in each
transaction's stated currency; mixing currencies within one position is flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from croesus.portfolio.models import Holding
from croesus.portfolio.transactions import (
    TXN_BUY,
    TXN_DEPOSIT,
    TXN_DIVIDEND,
    TXN_FEE,
    TXN_MANUAL_ADJUSTMENT,
    TXN_SELL,
    TXN_WITHDRAWAL,
    PortfolioTransaction,
)

# Quantities/cash below this magnitude are treated as zero to absorb float drift.
_EPSILON = 1e-9


@dataclass(frozen=True)
class DerivedHoldings:
    """Result of folding transactions into positions."""

    holdings: list[Holding]
    realized_pnl: float
    dividend_income: float
    cash_by_currency: dict[str, float]
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Position:
    quantity: float = 0.0
    cost_basis: float = 0.0
    currency: str | None = None


def derive_holdings_from_transactions(
    transactions: list[PortfolioTransaction],
    *,
    portfolio_id: str,
    as_of_date: date,
    base_currency: str = "USD",
    include_cash: bool = True,
) -> DerivedHoldings:
    """Fold ``transactions`` into derived holdings for ``portfolio_id``.

    Transactions are processed in ``(transaction_date, transaction_id)`` order so
    the result is deterministic regardless of input ordering. Only transactions
    dated on or before ``as_of_date`` are applied. Positions and cash balances
    that net to zero are dropped from the output.
    """
    ordered = sorted(
        (t for t in transactions if t.transaction_date <= as_of_date),
        key=lambda t: (t.transaction_date, t.transaction_id),
    )

    positions: dict[str, _Position] = {}
    cash: dict[str, float] = {}
    realized_pnl = 0.0
    dividend_income = 0.0
    warnings: list[str] = []

    def pos_for(asset_id: str, currency: str) -> _Position:
        p = positions.get(asset_id)
        if p is None:
            p = _Position(currency=currency)
            positions[asset_id] = p
        elif p.currency and p.currency != currency:
            warnings.append(
                f"{asset_id}: transaction currency {currency} differs from "
                f"position currency {p.currency}; using {p.currency}"
            )
        return p

    def add_cash(currency: str, amount: float) -> None:
        cash[currency] = cash.get(currency, 0.0) + amount

    for txn in ordered:
        currency = (txn.currency or base_currency).upper()
        fees = txn.fees or 0.0
        ttype = txn.transaction_type

        if ttype == TXN_BUY:
            p = pos_for(txn.asset_id, currency)
            notional = (txn.quantity or 0.0) * (txn.price or 0.0)
            p.quantity += txn.quantity or 0.0
            p.cost_basis += notional + fees
            add_cash(p.currency or currency, -(notional + fees))

        elif ttype == TXN_SELL:
            p = pos_for(txn.asset_id, currency)
            want = txn.quantity or 0.0
            sell_qty = want
            if want > p.quantity + _EPSILON:
                warnings.append(
                    f"{txn.asset_id}: sell of {want:g} exceeds held "
                    f"{p.quantity:g}; clamped to held quantity"
                )
                sell_qty = max(p.quantity, 0.0)
            avg_cost = p.cost_basis / p.quantity if p.quantity > _EPSILON else 0.0
            cost_removed = avg_cost * sell_qty
            proceeds = sell_qty * (txn.price or 0.0)
            realized_pnl += proceeds - cost_removed - fees
            p.quantity -= sell_qty
            p.cost_basis -= cost_removed
            add_cash(p.currency or currency, proceeds - fees)

        elif ttype == TXN_DIVIDEND:
            amount = txn.gross_amount or 0.0
            dividend_income += amount
            add_cash(currency, amount)

        elif ttype == TXN_FEE:
            fee = txn.fees if txn.fees is not None else (txn.gross_amount or 0.0)
            if txn.asset_id:
                pos_for(txn.asset_id, currency).cost_basis += fee
            add_cash(currency, -fee)

        elif ttype == TXN_DEPOSIT:
            add_cash(currency, txn.gross_amount or 0.0)

        elif ttype == TXN_WITHDRAWAL:
            add_cash(currency, -(txn.gross_amount or 0.0))

        elif ttype == TXN_MANUAL_ADJUSTMENT:
            p = pos_for(txn.asset_id, currency)
            delta = txn.quantity or 0.0
            if txn.price is not None:
                p.cost_basis += delta * txn.price
            elif delta < 0 and p.quantity > _EPSILON:
                # Reduce basis at the running average so avg_cost is preserved.
                p.cost_basis += (p.cost_basis / p.quantity) * delta
            elif delta > 0:
                warnings.append(
                    f"{txn.asset_id}: manual_adjustment adds {delta:g} shares "
                    "without a price; cost basis left unchanged"
                )
            p.quantity += delta

    holdings = _build_holdings(
        positions, portfolio_id=portfolio_id, as_of_date=as_of_date
    )
    cash_by_currency = {c: v for c, v in cash.items() if abs(v) > _EPSILON}
    if include_cash:
        holdings.extend(
            _cash_holdings(cash_by_currency, portfolio_id, as_of_date)
        )

    return DerivedHoldings(
        holdings=holdings,
        realized_pnl=realized_pnl,
        dividend_income=dividend_income,
        cash_by_currency=cash_by_currency,
        warnings=warnings,
    )


def _build_holdings(
    positions: dict[str, _Position],
    *,
    portfolio_id: str,
    as_of_date: date,
) -> list[Holding]:
    holdings: list[Holding] = []
    for asset_id in sorted(positions):
        p = positions[asset_id]
        if abs(p.quantity) <= _EPSILON:
            continue  # position fully closed
        avg_cost = p.cost_basis / p.quantity if p.quantity > _EPSILON else None
        holdings.append(
            Holding(
                portfolio_id=portfolio_id,
                asset_id=asset_id,
                as_of_date=as_of_date,
                quantity=p.quantity,
                market_value=None,  # filled by mark-to-market downstream
                currency=p.currency or "USD",
                cost_basis=p.cost_basis,
                avg_cost=avg_cost,
                source="derived",
            )
        )
    return holdings


def _cash_holdings(
    cash_by_currency: dict[str, float],
    portfolio_id: str,
    as_of_date: date,
) -> list[Holding]:
    """Cash balances as ``CASH_<CUR>`` holdings (value == quantity, cost == 1)."""
    out: list[Holding] = []
    for currency in sorted(cash_by_currency):
        balance = cash_by_currency[currency]
        out.append(
            Holding(
                portfolio_id=portfolio_id,
                asset_id=f"CASH_{currency}",
                as_of_date=as_of_date,
                quantity=balance,
                market_value=balance,
                currency=currency,
                cost_basis=balance,
                avg_cost=1.0,
                source="derived",
            )
        )
    return out
