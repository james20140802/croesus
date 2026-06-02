from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Callable

from croesus.fx.convert import to_base
from croesus.portfolio.models import (
    AssetAttrs,
    Holding,
    MarkToMarketResult,
    is_cash,
)


def mark_to_market(
    raw_holdings: list[Holding],
    price_lookup: Callable[[str], float | None],
    fx_rates: dict[str, float],
    assets_by_id: dict[str, AssetAttrs],
    *,
    base_currency: str,
    as_of_date: date,
) -> MarkToMarketResult:
    marked: list[Holding] = []
    warnings: list[str] = []

    normalized_rates = {k.upper(): v for k, v in fx_rates.items()}
    normalized_rates.setdefault("USD", 1.0)

    for holding in raw_holdings:
        native_currency = _native_currency(holding, assets_by_id, base_currency)
        native_mv, price_source, price_warning = _native_market_value(
            holding, price_lookup
        )
        if price_warning:
            warnings.append(price_warning)

        native_cost = _native_cost_basis(holding, native_mv)
        if _rate_missing(native_currency, normalized_rates):
            warnings.append(
                f"FX_MISSING {holding.asset_id}: no {native_currency} rate on or before {as_of_date}; using 1:1"
            )
        if _rate_missing(base_currency, normalized_rates):
            warnings.append(
                f"FX_MISSING {holding.asset_id}: no {base_currency} base rate on or before {as_of_date}; using 1:1"
            )

        market_value = to_base(
            native_mv,
            native_currency=native_currency,
            base_currency=base_currency,
            rates=normalized_rates,
        )
        cost_basis = (
            to_base(
                native_cost,
                native_currency=native_currency,
                base_currency=base_currency,
                rates=normalized_rates,
            )
            if native_cost is not None
            else None
        )
        unrealized_pnl = (
            market_value - cost_basis if cost_basis is not None else None
        )
        return_pct = (
            unrealized_pnl / cost_basis
            if unrealized_pnl is not None and cost_basis not in (None, 0)
            else None
        )
        metadata = dict(holding.metadata)
        metadata.update(
            {
                "price_source": price_source,
                "native_market_value": native_mv,
                "native_currency": native_currency,
                "as_of_date": as_of_date.isoformat(),
            }
        )
        if unrealized_pnl is not None:
            metadata["unrealized_pnl"] = unrealized_pnl
        if return_pct is not None:
            metadata["return_pct"] = return_pct

        marked.append(
            replace(
                holding,
                as_of_date=as_of_date,
                market_value=market_value,
                cost_basis=cost_basis,
                currency=native_currency,
                metadata=metadata,
            )
        )

    total_market_value = sum(h.market_value or 0.0 for h in marked)
    total_cost_basis = sum(h.cost_basis or 0.0 for h in marked)
    return MarkToMarketResult(
        holdings=marked,
        total_market_value=total_market_value,
        total_cost_basis=total_cost_basis,
        unrealized_pnl=total_market_value - total_cost_basis,
        warnings=warnings,
    )


def _native_currency(
    holding: Holding,
    assets_by_id: dict[str, AssetAttrs],
    base_currency: str,
) -> str:
    if holding.currency:
        return holding.currency.upper()
    attrs = assets_by_id.get(holding.asset_id)
    if attrs and attrs.currency:
        return attrs.currency.upper()
    if is_cash(holding.asset_id):
        currency = holding.asset_id.removeprefix("CASH_")
        if currency:
            return currency.upper()
    return base_currency.upper()


def _native_market_value(
    holding: Holding,
    price_lookup: Callable[[str], float | None],
) -> tuple[float, str, str | None]:
    if is_cash(holding.asset_id):
        return holding.market_value or 0.0, "cash", None

    close = price_lookup(holding.asset_id)
    if close is not None:
        if holding.quantity != 0:
            return holding.quantity * close, "store", None
        if holding.market_value is not None:
            return (
                holding.market_value,
                "manual",
                f"QUANTITY_MISSING {holding.asset_id}: quantity missing; using manual market_value",
            )

    if holding.market_value is not None:
        return (
            holding.market_value,
            "manual",
            f"PRICE_MISSING {holding.asset_id}: latest close missing; using manual market_value",
        )

    fallback = holding.quantity * (holding.avg_cost or 0.0)
    return (
        fallback,
        "cost_basis",
        f"PRICE_MISSING {holding.asset_id}: latest close missing; using cost_basis fallback",
    )


def _native_cost_basis(holding: Holding, native_mv: float) -> float | None:
    if is_cash(holding.asset_id):
        return native_mv
    if holding.avg_cost is not None:
        return holding.quantity * holding.avg_cost
    return holding.cost_basis


def _rate_missing(currency: str, rates: dict[str, float]) -> bool:
    return currency.upper() not in rates
