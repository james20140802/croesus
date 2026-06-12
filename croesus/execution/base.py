"""
Execution contracts (Sprint 013).

``BrokerAdapter`` is the seam between the approval gate and any venue. The
paper broker is the first implementation; a live broker would implement the
same Protocol. The execution job — not the adapter — owns every safety check
(approved, unexpired, not already executed), so no adapter can be reached by
an unapproved action.

Orders are expressed in notional terms because proposals carry an estimated
trade value, not share counts; the adapter resolves quantity at its fill
price.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

SIDE_BUY = "buy"
SIDE_SELL = "sell"


class ExecutionBlocked(RuntimeError):
    """The action may not be executed (not approved, expired, or already filled)."""


class ExecutionFailed(RuntimeError):
    """The broker could not fill an executable order (e.g. no price available)."""


@dataclass(frozen=True)
class OrderRequest:
    action_id: str
    portfolio_id: str
    asset_id: str
    side: str  # SIDE_BUY | SIDE_SELL
    notional: float  # base-currency trade value, always positive
    as_of_date: date


@dataclass(frozen=True)
class Fill:
    action_id: str
    asset_id: str
    side: str
    quantity: float
    price: float
    fees: float
    fill_date: date
    venue: str


class BrokerAdapter(Protocol):
    venue_name: str

    def submit(self, order: OrderRequest) -> Fill:
        """Fill the order or raise :class:`ExecutionFailed`."""
        ...
