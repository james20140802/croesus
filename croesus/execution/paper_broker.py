"""
Paper broker (Sprint 013): deterministic simulated fills, no external venue.

Fills at the latest stored close on or before the order date — the same price
surface every other module reads — with zero slippage and configurable flat
fees. No network, no order book, no partial fills: the goal is a truthful
"what would have happened" record in the ledger, not market microstructure.
"""
from __future__ import annotations

import duckdb

from croesus.execution.base import ExecutionFailed, Fill, OrderRequest
from croesus.prices.repository import PriceRepository


class PaperBroker:
    venue_name = "paper"

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        *,
        flat_fee: float = 0.0,
    ) -> None:
        self._prices = PriceRepository(conn)
        self._flat_fee = flat_fee

    def submit(self, order: OrderRequest) -> Fill:
        price = self._prices.get_latest_close(order.asset_id, order.as_of_date)
        if price is None or price <= 0:
            raise ExecutionFailed(
                f"no stored price for {order.asset_id} on or before "
                f"{order.as_of_date} — run daily_run first"
            )
        quantity = order.notional / price
        return Fill(
            action_id=order.action_id,
            asset_id=order.asset_id,
            side=order.side,
            quantity=quantity,
            price=price,
            fees=self._flat_fee,
            fill_date=order.as_of_date,
            venue=self.venue_name,
        )
