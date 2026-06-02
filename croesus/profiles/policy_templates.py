from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from croesus.profiles.models import PolicyTarget, PolicyTemplate

_CORE_METADATA = {
    "asset_types": ["etf"],
    "theme_tags": ["broad_market"],
}
_SATELLITE_METADATA = {
    "asset_types": ["equity", "etf"],
}
_DEFENSIVE_METADATA = {
    "asset_types": ["bond_etf"],
}
_CASH_METADATA = {
    "asset_types": ["cash"],
    "asset_ids": ["CASH_USD"],
}


POLICY_TEMPLATES: dict[str, PolicyTemplate] = {
    "growth_long_term": PolicyTemplate(
        template_id="growth_long_term",
        name="Growth long-term",
        description="Long horizon with higher drawdown tolerance and more satellite equity.",
        targets=[
            PolicyTarget(
                "template",
                "core_us_equity",
                0.60,
                0.50,
                0.70,
                metadata=_CORE_METADATA,
            ),
            PolicyTarget(
                "template",
                "satellite_equity",
                0.20,
                0.05,
                0.25,
                metadata=_SATELLITE_METADATA,
            ),
            PolicyTarget(
                "template",
                "defensive_bonds",
                0.10,
                0.05,
                0.20,
                metadata=_DEFENSIVE_METADATA,
            ),
            PolicyTarget(
                "template",
                "cash",
                0.10,
                0.05,
                0.15,
                metadata=_CASH_METADATA,
            ),
        ],
    ),
    "balanced_long_term": PolicyTemplate(
        template_id="balanced_long_term",
        name="Balanced long-term",
        description="Moderate long-term allocation with equity, defensive bonds, and cash.",
        targets=[
            PolicyTarget(
                "template",
                "core_us_equity",
                0.55,
                0.45,
                0.65,
                metadata=_CORE_METADATA,
            ),
            PolicyTarget(
                "template",
                "satellite_equity",
                0.15,
                0.00,
                0.20,
                metadata=_SATELLITE_METADATA,
            ),
            PolicyTarget(
                "template",
                "defensive_bonds",
                0.20,
                0.10,
                0.30,
                metadata=_DEFENSIVE_METADATA,
            ),
            PolicyTarget(
                "template",
                "cash",
                0.10,
                0.05,
                0.20,
                metadata=_CASH_METADATA,
            ),
        ],
    ),
    "capital_preservation": PolicyTemplate(
        template_id="capital_preservation",
        name="Capital preservation",
        description="Lower drawdown tolerance with larger defensive and cash sleeves.",
        targets=[
            PolicyTarget(
                "template",
                "core_us_equity",
                0.35,
                0.25,
                0.45,
                metadata=_CORE_METADATA,
            ),
            PolicyTarget(
                "template",
                "satellite_equity",
                0.05,
                0.00,
                0.10,
                metadata=_SATELLITE_METADATA,
            ),
            PolicyTarget(
                "template",
                "defensive_bonds",
                0.40,
                0.30,
                0.50,
                metadata=_DEFENSIVE_METADATA,
            ),
            PolicyTarget(
                "template",
                "cash",
                0.20,
                0.10,
                0.30,
                metadata=_CASH_METADATA,
            ),
        ],
    ),
}


def get_policy_template(template_id: str) -> PolicyTemplate:
    try:
        return POLICY_TEMPLATES[template_id]
    except KeyError as exc:
        raise ValueError(f"unknown policy template {template_id!r}") from exc


def instantiate_template(template: PolicyTemplate, profile_id: str) -> list[PolicyTarget]:
    return [
        replace(target, profile_id=profile_id, metadata=deepcopy(target.metadata))
        for target in template.targets
    ]
