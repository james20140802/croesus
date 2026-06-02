from dataclasses import replace

import pytest

from croesus.profiles.models import AssetType, PolicyTarget
from croesus.profiles.onboarding import recommend_policy
from croesus.profiles.policy_templates import POLICY_TEMPLATES
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE
from croesus.profiles.validation import validate_policy_targets


def test_growth_profile_recommends_growth_long_term_policy() -> None:
    profile = replace(
        DEFAULT_PROFILE,
        profile_id="growth",
        expected_annual_return=0.11,
        max_tolerable_drawdown=-0.30,
        investment_horizon_years=15,
    )

    recommendation = recommend_policy(profile)

    assert recommendation.profile_id == "growth"
    assert recommendation.template_id == "growth_long_term"
    assert sum(target.target_weight for target in recommendation.targets) == pytest.approx(1.0)
    assert {target.profile_id for target in recommendation.targets} == {"growth"}
    satellite = next(
        t for t in recommendation.targets if t.sleeve_name == "satellite_equity"
    )
    assert satellite.target_weight == 0.20


def test_capital_preservation_profile_recommends_defensive_policy() -> None:
    profile = replace(
        DEFAULT_PROFILE,
        profile_id="defensive",
        expected_annual_return=0.05,
        max_tolerable_drawdown=-0.08,
        investment_horizon_years=3,
        liquidity_buffer_months=18.0,
    )

    recommendation = recommend_policy(profile)

    assert recommendation.template_id == "capital_preservation"
    defensive = next(
        t for t in recommendation.targets if t.sleeve_name == "defensive_bonds"
    )
    cash = next(t for t in recommendation.targets if t.sleeve_name == "cash")
    assert defensive.target_weight == 0.40
    assert cash.target_weight == 0.20


def test_policy_template_targets_are_valid_and_have_mapping_metadata() -> None:
    for template in POLICY_TEMPLATES.values():
        result = validate_policy_targets(template.targets)

        assert result.is_valid
        assert result.errors == []
        assert not any("cash sleeve" in warning for warning in result.warnings)
        assert all(target.metadata for target in template.targets)


def test_recommendation_warns_when_profile_does_not_allow_cash() -> None:
    profile = replace(
        DEFAULT_PROFILE,
        profile_id="no_cash",
        allowed_asset_types=[AssetType.EQUITY, AssetType.ETF],
    )

    recommendation = recommend_policy(profile)

    assert any("cash" in warning.lower() for warning in recommendation.warnings)


def test_policy_target_validation_reports_ordering_errors() -> None:
    result = validate_policy_targets(
        [PolicyTarget("p", "core_us_equity", 0.50, 0.60, 0.70)]
    )

    assert not result.is_valid
    assert any(
        "core_us_equity" in error and "min_weight" in error
        for error in result.errors
    )


def test_policy_target_validation_warns_on_missing_cash_and_metadata() -> None:
    result = validate_policy_targets(
        [
            PolicyTarget("p", "core_us_equity", 0.60, 0.50, 0.70),
            PolicyTarget("p", "defensive_bonds", 0.40, 0.30, 0.50),
        ]
    )

    assert result.is_valid
    assert any("cash sleeve" in warning for warning in result.warnings)
    assert any("metadata" in warning for warning in result.warnings)


def test_policy_target_validation_does_not_treat_cash_prefix_as_cash_sleeve() -> None:
    result = validate_policy_targets(
        [
            PolicyTarget(
                "p",
                "rewards_equity",
                1.0,
                0.0,
                1.0,
                metadata={"asset_ids": ["CASHBACK_ETF"]},
            ),
        ]
    )

    assert result.is_valid
    assert any("cash sleeve" in warning for warning in result.warnings)
