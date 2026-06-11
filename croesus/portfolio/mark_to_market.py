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
from croesus.quality.models import (
    CODE_FX_MISSING,
    CODE_PRICE_MISSING,
    CODE_QUANTITY_MISSING,
    SEVERITY_ERROR,
    SEVERITY_WARN,
    DataQualityIssue,
)

_DOMAIN = "portfolio_snapshot"


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
    issues: list[DataQualityIssue] = []

    normalized_rates = {k.upper(): v for k, v in fx_rates.items()}
    normalized_rates.setdefault("USD", 1.0)

    for holding in raw_holdings:
        native_currency = _native_currency(holding, assets_by_id, base_currency)
        native_mv, price_source, price_issue = _native_market_value(
            holding, price_lookup, as_of_date
        )
        if price_issue:
            warnings.append(price_issue.message)
            issues.append(price_issue)

        native_cost = _native_cost_basis(holding, native_mv)
        fx_fallback = False
        for currency in {native_currency, base_currency.upper()}:
            if _rate_missing(currency, normalized_rates):
                fx_fallback = True
                message = (
                    f"FX_MISSING {holding.asset_id}: no {currency} rate on or "
                    f"before {as_of_date}; using 1:1"
                )
                warnings.append(message)
                issues.append(
                    DataQualityIssue(
                        domain=_DOMAIN,
                        severity=SEVERITY_ERROR,
                        code=CODE_FX_MISSING,
                        message=message,
                        asset_id=holding.asset_id,
                        currency=currency,
                        as_of_date=as_of_date,
                    )
                )

        # The 1:1 passthrough is only ever reached after the ERROR above has
        # been recorded — the snapshot still completes, but as DEGRADED.
        market_value = to_base(
            native_mv,
            native_currency=native_currency,
            base_currency=base_currency,
            rates=normalized_rates,
            fallback_to_one=fx_fallback,
        )
        cost_basis = (
            to_base(
                native_cost,
                native_currency=native_currency,
                base_currency=base_currency,
                rates=normalized_rates,
                fallback_to_one=fx_fallback,
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
        if fx_fallback:
            metadata["fx_missing"] = True
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
    total_cost_basis = (
        None
        if any(h.cost_basis is None for h in marked)
        else sum(h.cost_basis or 0.0 for h in marked)
    )
    unrealized_pnl = (
        None
        if total_cost_basis is None
        else total_market_value - total_cost_basis
    )
    return MarkToMarketResult(
        holdings=marked,
        total_market_value=total_market_value,
        total_cost_basis=total_cost_basis,
        unrealized_pnl=unrealized_pnl,
        warnings=warnings,
        issues=issues,
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
    as_of_date: date,
) -> tuple[float, str, DataQualityIssue | None]:
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
                _issue(
                    CODE_QUANTITY_MISSING,
                    SEVERITY_WARN,
                    f"QUANTITY_MISSING {holding.asset_id}: quantity missing; using manual market_value",
                    holding.asset_id,
                    as_of_date,
                ),
            )

    if holding.market_value is not None:
        return (
            holding.market_value,
            "manual",
            _issue(
                CODE_PRICE_MISSING,
                SEVERITY_ERROR,
                f"PRICE_MISSING {holding.asset_id}: latest close missing; using manual market_value",
                holding.asset_id,
                as_of_date,
            ),
        )

    fallback = holding.quantity * (holding.avg_cost or 0.0)
    return (
        fallback,
        "cost_basis",
        _issue(
            CODE_PRICE_MISSING,
            SEVERITY_ERROR,
            f"PRICE_MISSING {holding.asset_id}: latest close missing; using cost_basis fallback",
            holding.asset_id,
            as_of_date,
        ),
    )


def _issue(
    code: str,
    severity: str,
    message: str,
    asset_id: str,
    as_of_date: date,
) -> DataQualityIssue:
    return DataQualityIssue(
        domain=_DOMAIN,
        severity=severity,
        code=code,
        message=message,
        asset_id=asset_id,
        as_of_date=as_of_date,
    )


def _native_cost_basis(holding: Holding, native_mv: float) -> float | None:
    if is_cash(holding.asset_id):
        return native_mv
    if holding.avg_cost is not None:
        return holding.quantity * holding.avg_cost
    return holding.cost_basis


def _rate_missing(currency: str, rates: dict[str, float]) -> bool:
    return currency.upper() not in rates
