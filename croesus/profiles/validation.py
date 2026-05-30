from __future__ import annotations

from croesus.profiles.models import (
    InvestorProfile,
    PolicyTarget,
    ProfileValidationResult,
    TradeMode,
)

# trade_mode values accepted in the Level 1 MVP.
VALID_TRADE_MODES: frozenset[TradeMode] = frozenset(
    {TradeMode.PROPOSE_ONLY, TradeMode.APPROVAL_REQUIRED}
)

# Tolerance for the policy-target weight sum check.
_WEIGHT_SUM_TOLERANCE = 1e-9


def validate_profile(profile: InvestorProfile) -> ProfileValidationResult:
    """Check a profile for internal consistency.

    Errors block portfolio action generation in later sprints. Warnings flag
    questionable but structurally valid profiles and do not block.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if profile.expected_annual_return <= 0:
        errors.append("expected_annual_return must be positive")
    if profile.max_tolerable_drawdown >= 0:
        errors.append("max_tolerable_drawdown must be negative (a loss)")
    if profile.investment_horizon_years < 1:
        errors.append("investment_horizon_years must be at least 1")
    if profile.rebalance_band <= 0:
        errors.append("rebalance_band must be positive")
    if profile.max_monthly_turnover <= 0:
        errors.append("max_monthly_turnover must be positive")

    if profile.trade_mode == TradeMode.BOUNDED_AUTO:
        errors.append("trade_mode bounded_auto is not supported in the MVP")
    elif profile.trade_mode not in VALID_TRADE_MODES:
        errors.append(f"trade_mode must be one of {sorted(m.value for m in VALID_TRADE_MODES)}")

    if profile.max_single_position_weight > profile.max_sector_weight:
        warnings.append(
            "max_single_position_weight exceeds max_sector_weight"
        )
    if profile.max_tolerable_drawdown > -0.05 and profile.expected_annual_return > 0.08:
        warnings.append(
            "shallow drawdown tolerance with high expected return is unrealistic"
        )

    return ProfileValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
    )


def validate_policy_targets(targets: list[PolicyTarget]) -> ProfileValidationResult:
    """Ensure policy target weights form a valid allocation summing to 1.0."""
    errors: list[str] = []
    warnings: list[str] = []

    if not targets:
        errors.append("policy targets must not be empty")
        return ProfileValidationResult(is_valid=False, errors=errors, warnings=warnings)

    total = sum(target.target_weight for target in targets)
    if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
        errors.append(f"policy target weights must sum to 1.0 (got {total})")

    return ProfileValidationResult(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
    )
