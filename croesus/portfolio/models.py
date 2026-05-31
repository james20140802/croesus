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
    market_value: float
    currency: str
    cost_basis: float | None = None
    source: str | None = "manual_csv"
    metadata: dict[str, Any] = field(default_factory=dict)


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
class PortfolioSnapshotResult:
    portfolio_id: str
    as_of_date: date
    total_market_value: float
    holdings_imported: int
    holdings_skipped: int
    exposures: list[Exposure]
    policy_drifts: list[PolicyDrift]
    warnings: list[str]
