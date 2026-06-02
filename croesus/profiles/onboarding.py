from __future__ import annotations

from croesus.profiles.models import (
    AssetType,
    InvestorProfile,
    PolicyRecommendation,
)
from croesus.profiles.policy_templates import (
    get_policy_template,
    instantiate_template,
)
from croesus.profiles.validation import validate_policy_targets, validate_profile


def recommend_policy(profile: InvestorProfile) -> PolicyRecommendation:
    """Choose an editable starting policy from explicit profile constraints."""
    template_id, rationale = _select_template_id(profile)
    template = get_policy_template(template_id)
    targets = instantiate_template(template, profile.profile_id)

    profile_result = validate_profile(profile)
    target_result = validate_policy_targets(targets)
    warnings = [
        *profile_result.warnings,
        *target_result.warnings,
        *template.warnings,
        *_profile_asset_warnings(profile),
    ]

    return PolicyRecommendation(
        profile_id=profile.profile_id,
        template_id=template.template_id,
        targets=targets,
        rationale=rationale,
        warnings=warnings,
    )


def _select_template_id(profile: InvestorProfile) -> tuple[str, list[str]]:
    if (
        profile.max_tolerable_drawdown > -0.10
        or profile.investment_horizon_years <= 3
        or profile.liquidity_buffer_months >= 12
    ):
        return (
            "capital_preservation",
            [
                "selected capital_preservation because drawdown tolerance, horizon, "
                "or liquidity needs call for a larger defensive/cash allocation"
            ],
        )

    if (
        profile.investment_horizon_years >= 10
        and profile.max_tolerable_drawdown <= -0.20
        and profile.expected_annual_return >= 0.09
    ):
        return (
            "growth_long_term",
            [
                "selected growth_long_term because the profile has a long horizon, "
                "higher expected return, and deeper drawdown tolerance"
            ],
        )

    return (
        "balanced_long_term",
        [
            "selected balanced_long_term because the profile is within moderate "
            "return, horizon, and drawdown ranges"
        ],
    )


def _profile_asset_warnings(profile: InvestorProfile) -> list[str]:
    allowed = set(profile.allowed_asset_types)
    warnings: list[str] = []
    if AssetType.CASH not in allowed:
        warnings.append(
            "profile allowed_asset_types does not include cash; recommended templates "
            "still include a cash sleeve for liquidity and drift checks"
        )
    if AssetType.ETF not in allowed and AssetType.EQUITY not in allowed:
        warnings.append(
            "profile allowed_asset_types does not include equity or etf; recommended "
            "equity sleeves may need editing before save"
        )
    return warnings
