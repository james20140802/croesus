from __future__ import annotations

from typing import Any, Callable

from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    PolicyTarget,
    TradeMode,
)

InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]

_VALID_TRADE_MODES = (TradeMode.PROPOSE_ONLY, TradeMode.APPROVAL_REQUIRED)


def ask(
    input_fn: InputFn,
    output_fn: OutputFn,
    label: str,
    default: Any,
    parse: Callable[[str], Any],
) -> Any:
    """Prompt for one value, re-prompting until parse succeeds.

    Empty input accepts the default. ``parse`` should raise ValueError/KeyError
    on invalid input; the error is shown and the prompt repeats.
    """
    while True:
        raw = input_fn(f"{label} [{_display(default)}]: ").strip()
        if raw == "":
            return default
        try:
            return parse(raw)
        except (ValueError, KeyError) as exc:
            output_fn(f"  invalid value: {exc}")


def build_profile_interactively(
    profile_defaults: InvestorProfile,
    target_defaults: list[PolicyTarget],
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> tuple[InvestorProfile, list[PolicyTarget]]:
    """Walk the user through every profile field, then the policy targets."""
    output_fn("Investor profile setup — press Enter to accept the [default].")

    def q(label: str, default: Any, parse: Callable[[str], Any]) -> Any:
        return ask(input_fn, output_fn, label, default, parse)

    currency_help = f"base_currency ({', '.join(c.value for c in Currency)})"
    mode_help = f"trade_mode ({', '.join(m.value for m in _VALID_TRADE_MODES)})"

    profile = InvestorProfile(
        profile_id=q("profile_id", profile_defaults.profile_id, str),
        name=q("name", profile_defaults.name, str),
        base_currency=q(currency_help, profile_defaults.base_currency, _parse_currency),
        expected_annual_return=q(
            "expected_annual_return (e.g. 0.10)",
            profile_defaults.expected_annual_return,
            _positive_float,
        ),
        max_tolerable_drawdown=q(
            "max_tolerable_drawdown (negative, e.g. -0.25)",
            profile_defaults.max_tolerable_drawdown,
            _negative_float,
        ),
        investment_horizon_years=q(
            "investment_horizon_years",
            profile_defaults.investment_horizon_years,
            _positive_int,
        ),
        monthly_contribution=q(
            "monthly_contribution", profile_defaults.monthly_contribution, _nonnegative_float
        ),
        liquidity_buffer_months=q(
            "liquidity_buffer_months",
            profile_defaults.liquidity_buffer_months,
            _nonnegative_float,
        ),
        allowed_asset_types=q(
            "allowed_asset_types (comma-separated)",
            profile_defaults.allowed_asset_types,
            _parse_asset_types,
        ),
        disallowed_asset_types=q(
            "disallowed_asset_types (comma-separated)",
            profile_defaults.disallowed_asset_types,
            _parse_asset_types,
        ),
        max_single_position_weight=q(
            "max_single_position_weight", profile_defaults.max_single_position_weight, _fraction
        ),
        max_sector_weight=q("max_sector_weight", profile_defaults.max_sector_weight, _fraction),
        max_industry_weight=q(
            "max_industry_weight", profile_defaults.max_industry_weight, _fraction
        ),
        max_theme_weight=q("max_theme_weight", profile_defaults.max_theme_weight, _fraction),
        max_country_weight=q(
            "max_country_weight", profile_defaults.max_country_weight, _fraction
        ),
        max_currency_weight=q(
            "max_currency_weight", profile_defaults.max_currency_weight, _fraction
        ),
        max_monthly_turnover=q(
            "max_monthly_turnover", profile_defaults.max_monthly_turnover, _positive_float
        ),
        rebalance_band=q("rebalance_band", profile_defaults.rebalance_band, _positive_float),
        trade_mode=q(mode_help, profile_defaults.trade_mode, _parse_trade_mode),
        metadata=profile_defaults.metadata,
    )

    targets = _prompt_policy_targets(profile.profile_id, target_defaults, q, output_fn)
    return profile, targets


def _prompt_policy_targets(
    profile_id: str,
    target_defaults: list[PolicyTarget],
    q: Callable[[str, Any, Callable[[str], Any]], Any],
    output_fn: OutputFn,
) -> list[PolicyTarget]:
    output_fn("Policy targets — target weights must sum to 1.0.")
    while True:
        targets = []
        for default in target_defaults:
            sleeve = default.sleeve_name
            target_weight = q(f"{sleeve} target_weight", default.target_weight, _fraction)
            min_weight = q(f"{sleeve} min_weight", default.min_weight, _optional_fraction)
            max_weight = q(f"{sleeve} max_weight", default.max_weight, _optional_fraction)
            targets.append(
                PolicyTarget(
                    profile_id=profile_id,
                    sleeve_name=sleeve,
                    target_weight=target_weight,
                    min_weight=min_weight,
                    max_weight=max_weight,
                    metadata=default.metadata,
                )
            )
        total = sum(t.target_weight for t in targets)
        if abs(total - 1.0) <= 1e-9:
            return targets
        output_fn(f"  target weights sum to {total}; must be 1.0 — please re-enter.")


def _display(default: Any) -> str:
    if isinstance(default, list):
        return ", ".join(item.value if hasattr(item, "value") else str(item) for item in default)
    if hasattr(default, "value"):
        return str(default.value)
    return str(default)


def _positive_float(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise ValueError("must be greater than 0")
    return value


def _negative_float(raw: str) -> float:
    value = float(raw)
    if value >= 0:
        raise ValueError("must be negative")
    return value


def _nonnegative_float(raw: str) -> float:
    value = float(raw)
    if value < 0:
        raise ValueError("must be 0 or greater")
    return value


def _fraction(raw: str) -> float:
    value = float(raw)
    if not 0.0 <= value <= 1.0:
        raise ValueError("must be between 0 and 1")
    return value


def _optional_fraction(raw: str) -> float | None:
    if raw.strip().lower() in {"none", "null"}:
        return None
    return _fraction(raw)


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise ValueError("must be at least 1")
    return value


def _parse_currency(raw: str) -> Currency:
    try:
        return Currency(raw.strip().upper())
    except ValueError as exc:
        raise ValueError(f"allowed: {', '.join(c.value for c in Currency)}") from exc


def _parse_trade_mode(raw: str) -> TradeMode:
    try:
        mode = TradeMode(raw.strip())
    except ValueError as exc:
        raise ValueError(
            f"allowed: {', '.join(m.value for m in _VALID_TRADE_MODES)}"
        ) from exc
    if mode not in _VALID_TRADE_MODES:
        raise ValueError(f"{mode.value} is not supported in the MVP")
    return mode


def _parse_asset_types(raw: str) -> list[AssetType]:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    result = []
    for part in parts:
        try:
            result.append(AssetType(part))
        except ValueError as exc:
            raise ValueError(
                f"unknown asset type {part!r}; allowed: {', '.join(a.value for a in AssetType)}"
            ) from exc
    return result
