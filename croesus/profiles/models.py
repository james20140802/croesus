from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TradeMode(str, Enum):
    """Execution mode the system is allowed to operate under."""

    PROPOSE_ONLY = "propose_only"
    APPROVAL_REQUIRED = "approval_required"
    BOUNDED_AUTO = "bounded_auto"  # future mode; rejected by MVP validation


class AssetType(str, Enum):
    """Asset classes a profile may allow or disallow."""

    EQUITY = "equity"
    ETF = "etf"
    REIT = "reit"
    CASH = "cash"
    OPTION = "option"
    LEVERAGED_ETF = "leveraged_etf"
    SHORT_POSITION = "short_position"


class Currency(str, Enum):
    """Base currencies supported in the MVP (pragmatic subset of ISO 4217)."""

    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    JPY = "JPY"
    KRW = "KRW"
    CNY = "CNY"
    HKD = "HKD"
    CAD = "CAD"
    AUD = "AUD"
    CHF = "CHF"


@dataclass(frozen=True)
class InvestorProfile:
    profile_id: str
    name: str
    base_currency: Currency
    expected_annual_return: float
    max_tolerable_drawdown: float
    investment_horizon_years: int
    monthly_contribution: float
    liquidity_buffer_months: float
    allowed_asset_types: list[AssetType]
    disallowed_asset_types: list[AssetType]
    max_single_position_weight: float
    max_sector_weight: float
    max_industry_weight: float
    max_theme_weight: float
    max_country_weight: float
    max_currency_weight: float
    max_monthly_turnover: float
    rebalance_band: float
    trade_mode: TradeMode
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyTarget:
    profile_id: str
    sleeve_name: str
    target_weight: float
    min_weight: float | None = None
    max_weight: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]
