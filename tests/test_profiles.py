from pathlib import Path
from typing import Any

import pytest

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    TradeMode,
)
from croesus.profiles.models import PolicyTarget
from croesus.profiles.repository import ProfileRepository
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


def test_profile_repository_round_trips_json_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)
    profile = _valid_profile(metadata={"note": "test", "tags": ["a", "b"]})

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        repo.upsert_profile(profile)
        loaded = repo.get_profile("default")

    assert loaded == profile
    assert loaded.base_currency is Currency.USD
    assert loaded.trade_mode is TradeMode.PROPOSE_ONLY
    assert loaded.allowed_asset_types == [
        AssetType.EQUITY,
        AssetType.ETF,
        AssetType.REIT,
        AssetType.CASH,
    ]
    assert loaded.metadata == {"note": "test", "tags": ["a", "b"]}


def test_profile_repository_get_missing_profile_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        assert ProfileRepository(conn).get_profile("missing") is None


def test_profile_repository_upsert_preserves_created_at(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        repo.upsert_profile(_valid_profile())
        created_first = conn.execute(
            "SELECT created_at FROM investor_profiles WHERE profile_id = 'default'"
        ).fetchone()[0]
        repo.upsert_profile(_valid_profile(name="renamed"))
        created_again, name = conn.execute(
            "SELECT created_at, name FROM investor_profiles WHERE profile_id = 'default'"
        ).fetchone()

    assert created_again == created_first
    assert name == "renamed"


def test_profile_repository_round_trips_policy_targets(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)
    targets = [
        PolicyTarget("default", "core_us_equity", 0.55, 0.45, 0.65),
        PolicyTarget("default", "satellite_equity", 0.15, 0.00, 0.20),
        PolicyTarget("default", "defensive_bonds", 0.20, 0.10, 0.30),
        PolicyTarget("default", "cash", 0.10, 0.05, 0.20),
    ]

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        repo.upsert_policy_targets(targets)
        loaded = repo.get_policy_targets("default")

    assert {t.sleeve_name for t in loaded} == {t.sleeve_name for t in targets}
    assert len(loaded) == 4
    core = next(t for t in loaded if t.sleeve_name == "core_us_equity")
    assert core.target_weight == 0.55
    assert core.min_weight == 0.45
    assert core.max_weight == 0.65


def test_replace_policy_targets_removes_stale_sleeves(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)
    original = [
        PolicyTarget("default", "core_us_equity", 0.55, 0.45, 0.65),
        PolicyTarget("default", "satellite_equity", 0.15, 0.00, 0.20),
        PolicyTarget("default", "defensive_bonds", 0.20, 0.10, 0.30),
        PolicyTarget("default", "cash", 0.10, 0.05, 0.20),
    ]
    replacement = [
        PolicyTarget("default", "core_us_equity", 0.60, 0.50, 0.70),
        PolicyTarget("default", "defensive_bonds", 0.40, 0.30, 0.50),
    ]

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        repo.upsert_policy_targets(original)
        repo.replace_policy_targets("default", replacement)
        loaded = repo.get_policy_targets("default")

    assert {t.sleeve_name for t in loaded} == {"core_us_equity", "defensive_bonds"}
    core = next(t for t in loaded if t.sleeve_name == "core_us_equity")
    assert core.target_weight == 0.60


def test_save_profile_persists_profile_and_targets(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)
    profile = _valid_profile(profile_id="p")
    targets = [
        PolicyTarget("p", "core_us_equity", 0.6, 0.5, 0.7),
        PolicyTarget("p", "defensive_bonds", 0.4, 0.3, 0.5),
    ]

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        repo.save_profile(profile, targets)
        loaded = repo.get_profile("p")
        sleeves = {t.sleeve_name for t in repo.get_policy_targets("p")}

    assert loaded == profile
    assert sleeves == {"core_us_equity", "defensive_bonds"}


def test_save_profile_rolls_back_profile_when_targets_fail(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)
    profile = _valid_profile(profile_id="p")
    # target_weight is NOT NULL: None forces the target insert to fail.
    bad_targets = [PolicyTarget("p", "core_us_equity", None, None, None)]  # type: ignore[arg-type]

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        with pytest.raises(Exception):
            repo.save_profile(profile, bad_targets)
        # the profile write must roll back too — not a half-applied save
        assert repo.get_profile("p") is None


def test_save_profile_rejects_mismatched_target_profile_id(tmp_path: Path) -> None:
    db_path = tmp_path / "profiles.duckdb"
    migrate(db_path)
    profile = _valid_profile(profile_id="p")
    # target belongs to a different profile ("q") than the one being saved ("p")
    mismatched = [PolicyTarget("q", "core_us_equity", 1.0, None, None)]

    with get_connection(db_path) as conn:
        repo = ProfileRepository(conn)
        with pytest.raises(ValueError):
            repo.save_profile(profile, mismatched)
        # guard runs before any write: nothing persisted for either id
        assert repo.get_profile("p") is None
        assert repo.get_policy_targets("p") == []
        assert repo.get_policy_targets("q") == []
