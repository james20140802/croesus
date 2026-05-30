from pathlib import Path
from typing import Any

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    TradeMode,
)
from croesus.profiles.validation import validate_profile


def _valid_profile(**overrides: Any) -> InvestorProfile:
    """Build a structurally valid profile matching the default seed values."""
    fields: dict[str, Any] = dict(
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
    fields.update(overrides)
    return InvestorProfile(**fields)


def test_migrate_creates_profile_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"

    migrate(db_path)

    with get_connection(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

    assert {"investor_profiles", "policy_targets"} <= tables


def test_validate_accepts_valid_default_profile() -> None:
    result = validate_profile(_valid_profile())

    assert result.is_valid
    assert result.errors == []


def test_validate_rejects_non_negative_drawdown() -> None:
    result = validate_profile(_valid_profile(max_tolerable_drawdown=0.0))

    assert not result.is_valid
    assert any("drawdown" in err.lower() for err in result.errors)


def test_validate_rejects_bounded_auto_trade_mode() -> None:
    result = validate_profile(_valid_profile(trade_mode=TradeMode.BOUNDED_AUTO))

    assert not result.is_valid
    assert any("bounded_auto" in err.lower() for err in result.errors)


def test_validate_warns_on_unrealistic_return_drawdown_combo() -> None:
    # drawdown shallow (-0.02) with high return (0.10): warning only, no errors.
    result = validate_profile(
        _valid_profile(max_tolerable_drawdown=-0.02, expected_annual_return=0.10)
    )

    assert result.is_valid
    assert result.errors == []
    assert result.warnings != []
