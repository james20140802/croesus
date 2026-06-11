from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Portfolio:
    portfolio_id: str
    profile_id: str
    name: str
    base_currency: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Holding:
    portfolio_id: str
    asset_id: str
    as_of_date: date
    quantity: float
    market_value: float | None
    currency: str
    cost_basis: float | None = None
    avg_cost: float | None = None
    source: str | None = "manual_csv"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetAttrs:
    """Classification attributes for one asset, used by exposure/drift math.

    Built from the ``assets`` table (and a synthetic entry for cash). Decoupled
    from the persisted ``Asset`` model so the computation functions stay pure
    and trivially testable.
    """

    asset_type: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    currency: str | None = None
    theme_tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Exposure:
    portfolio_id: str
    as_of_date: date
    exposure_type: str
    exposure_name: str
    weight: float
    market_value: float
    limit_weight: float | None
    is_violation: bool


@dataclass(frozen=True)
class PolicyDrift:
    portfolio_id: str
    as_of_date: date
    sleeve_name: str
    current_weight: float
    target_weight: float
    min_weight: float | None
    max_weight: float | None
    drift: float
    is_outside_band: bool


@dataclass(frozen=True)
class MarkToMarketResult:
    holdings: list[Holding]
    total_market_value: float
    total_cost_basis: float | None
    unrealized_pnl: float | None
    warnings: list[str]
    # Structured ERROR/WARN issues behind the warning strings (Sprint 008a).
    # Any ERROR here means a value above is misstated -> snapshot is DEGRADED.
    issues: list[Any] = field(default_factory=list)


@dataclass(frozen=True)
class ResolverStatus:
    """Row-level asset resolution status for app-ready import feedback."""

    row_number: int
    status: str
    symbol: str | None = None
    asset_id: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class PortfolioSnapshotResult:
    portfolio_id: str
    as_of_date: date
    total_market_value: float
    total_cost_basis: float | None
    unrealized_pnl: float | None
    holdings_imported: int
    holdings_skipped: int
    exposures: list[Exposure]
    policy_drifts: list[PolicyDrift]
    warnings: list[str]
    resolver_statuses: list[ResolverStatus] = field(default_factory=list)
    # ERROR-level data-quality issues persisted for this snapshot; non-empty
    # means the snapshot is DEGRADED (some value relies on a fallback).
    data_quality_errors: list[Any] = field(default_factory=list)


def is_cash(asset_id: str) -> bool:
    return asset_id.startswith("CASH_")
