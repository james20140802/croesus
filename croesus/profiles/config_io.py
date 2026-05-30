from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from croesus.profiles.models import (
    AssetType,
    Currency,
    InvestorProfile,
    PolicyTarget,
    TradeMode,
)

_TEMPLATE_HEADER = (
    "# Croesus investor profile config\n"
    "# Edit the values below, then load with:\n"
    "#   python -m croesus.jobs.profile_init --config <this file>\n"
)


def profile_to_config(
    profile: InvestorProfile,
    targets: list[PolicyTarget],
) -> dict[str, Any]:
    """Serialize a profile + policy targets into a YAML-friendly dict."""
    return {
        "profile": {
            "profile_id": profile.profile_id,
            "name": profile.name,
            "base_currency": profile.base_currency.value,
            "expected_annual_return": profile.expected_annual_return,
            "max_tolerable_drawdown": profile.max_tolerable_drawdown,
            "investment_horizon_years": profile.investment_horizon_years,
            "monthly_contribution": profile.monthly_contribution,
            "liquidity_buffer_months": profile.liquidity_buffer_months,
            "allowed_asset_types": [t.value for t in profile.allowed_asset_types],
            "disallowed_asset_types": [t.value for t in profile.disallowed_asset_types],
            "max_single_position_weight": profile.max_single_position_weight,
            "max_sector_weight": profile.max_sector_weight,
            "max_industry_weight": profile.max_industry_weight,
            "max_theme_weight": profile.max_theme_weight,
            "max_country_weight": profile.max_country_weight,
            "max_currency_weight": profile.max_currency_weight,
            "max_monthly_turnover": profile.max_monthly_turnover,
            "rebalance_band": profile.rebalance_band,
            "trade_mode": profile.trade_mode.value,
            "metadata": profile.metadata,
        },
        "policy_targets": [
            {
                "sleeve_name": t.sleeve_name,
                "target_weight": t.target_weight,
                "min_weight": t.min_weight,
                "max_weight": t.max_weight,
                "metadata": t.metadata,
            }
            for t in targets
        ],
    }


def config_to_profile(
    data: dict[str, Any],
) -> tuple[InvestorProfile, list[PolicyTarget]]:
    """Parse a config dict into a profile + policy targets.

    Raises ValueError on missing fields or invalid enum values.
    """
    if not isinstance(data, dict) or "profile" not in data:
        raise ValueError("config must contain a top-level 'profile' mapping")

    p = data["profile"]
    try:
        profile = InvestorProfile(
            profile_id=p["profile_id"],
            name=p["name"],
            base_currency=_enum(Currency, p["base_currency"], "base_currency"),
            expected_annual_return=float(p["expected_annual_return"]),
            max_tolerable_drawdown=float(p["max_tolerable_drawdown"]),
            investment_horizon_years=int(p["investment_horizon_years"]),
            monthly_contribution=float(p["monthly_contribution"]),
            liquidity_buffer_months=float(p["liquidity_buffer_months"]),
            allowed_asset_types=[
                _enum(AssetType, x, "allowed_asset_types") for x in p["allowed_asset_types"]
            ],
            disallowed_asset_types=[
                _enum(AssetType, x, "disallowed_asset_types")
                for x in p["disallowed_asset_types"]
            ],
            max_single_position_weight=float(p["max_single_position_weight"]),
            max_sector_weight=float(p["max_sector_weight"]),
            max_industry_weight=float(p["max_industry_weight"]),
            max_theme_weight=float(p["max_theme_weight"]),
            max_country_weight=float(p["max_country_weight"]),
            max_currency_weight=float(p["max_currency_weight"]),
            max_monthly_turnover=float(p["max_monthly_turnover"]),
            rebalance_band=float(p["rebalance_band"]),
            trade_mode=_enum(TradeMode, p["trade_mode"], "trade_mode"),
            metadata=p.get("metadata") or {},
        )
    except KeyError as exc:
        raise ValueError(f"profile is missing required field {exc}") from exc

    targets = [
        PolicyTarget(
            profile_id=profile.profile_id,
            sleeve_name=t["sleeve_name"],
            target_weight=float(t["target_weight"]),
            min_weight=None if t.get("min_weight") is None else float(t["min_weight"]),
            max_weight=None if t.get("max_weight") is None else float(t["max_weight"]),
            metadata=t.get("metadata") or {},
        )
        for t in data.get("policy_targets", [])
    ]
    return profile, targets


def read_profile_config(path: str | Path) -> tuple[InvestorProfile, list[PolicyTarget]]:
    """Load a profile config YAML file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return config_to_profile(data)


def write_profile_config(
    path: str | Path,
    profile: InvestorProfile,
    targets: list[PolicyTarget],
    *,
    overwrite: bool = False,
) -> None:
    """Write a profile config YAML file, refusing to clobber unless overwrite."""
    target = Path(path)
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} already exists (pass overwrite=True to replace)")
    target.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        profile_to_config(profile, targets),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    target.write_text(_TEMPLATE_HEADER + body, encoding="utf-8")


def _enum(enum_cls: type, value: Any, field: str) -> Any:
    try:
        return enum_cls(value)
    except ValueError as exc:
        allowed = [m.value for m in enum_cls]
        raise ValueError(
            f"invalid {field} {value!r}; allowed values: {allowed}"
        ) from exc
