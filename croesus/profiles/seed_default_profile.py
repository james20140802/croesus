from __future__ import annotations

import duckdb

from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    PolicyTarget,
    TradeMode,
)
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.validation import validate_policy_targets

DEFAULT_PROFILE = InvestorProfile(
    profile_id="default",
    name="Growth-oriented long-term taxable account",
    base_currency=Currency.USD,
    expected_annual_return=0.10,
    max_tolerable_drawdown=-0.25,
    investment_horizon_years=10,
    monthly_contribution=2000.0,
    liquidity_buffer_months=6.0,
    allowed_asset_types=[
        AssetType.EQUITY,
        AssetType.ETF,
        AssetType.REIT,
        AssetType.CASH,
    ],
    disallowed_asset_types=[
        AssetType.OPTION,
        AssetType.LEVERAGED_ETF,
        AssetType.SHORT_POSITION,
    ],
    max_single_position_weight=0.10,
    max_sector_weight=0.35,
    max_industry_weight=0.25,
    max_theme_weight=0.30,
    max_country_weight=0.90,
    max_currency_weight=0.95,
    max_monthly_turnover=0.15,
    rebalance_band=0.05,
    trade_mode=TradeMode.PROPOSE_ONLY,
)

# Sleeve metadata maps held assets to sleeves for portfolio drift (Sprint 004).
# Without it, every holding would fall through to the satellite fallback and
# drift would be meaningless for the out-of-the-box profile.
DEFAULT_POLICY_TARGETS = [
    PolicyTarget(
        "default", "core_us_equity", 0.55, 0.45, 0.65,
        metadata={"asset_types": ["etf"], "theme_tags": ["broad_market"]},
    ),
    PolicyTarget(
        "default", "satellite_equity", 0.15, 0.00, 0.20,
        metadata={"asset_types": ["equity"]},
    ),
    PolicyTarget(
        "default", "defensive_bonds", 0.20, 0.10, 0.30,
        metadata={"asset_types": ["bond_etf"]},
    ),
    PolicyTarget(
        "default", "cash", 0.10, 0.05, 0.20,
        metadata={"asset_ids": ["CASH_USD"]},
    ),
]


def seed_default_profile(conn: duckdb.DuckDBPyConnection) -> None:
    """Seed the default advanced profile and its policy targets (idempotent)."""
    result = validate_policy_targets(DEFAULT_POLICY_TARGETS)
    if not result.is_valid:
        raise ValueError(f"invalid default policy targets: {result.errors}")

    # save_profile writes profile + targets in one transaction and replaces
    # (not merges) targets, so a re-seed after a custom --config run leaves no
    # stale sleeves behind.
    ProfileRepository(conn).save_profile(DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS)
