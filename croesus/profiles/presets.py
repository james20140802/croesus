"""House risk presets surfaced in the settings UI.

Each preset is one editable risk band from ``risk_return_map.yaml`` rendered as
an :class:`InvestorProfile` draft plus its policy targets, so the user can load
it, review it, and then Save to apply. The band — never the LLM — is the single
source of every number, matching the guided onboarding flow.

A preset is only a *draft*: nothing is written until the user saves. Loading one
fills the form; the existing Save path persists it to the active profile.
"""
from __future__ import annotations

from dataclasses import replace

from croesus.profiles.guidance import RiskBand, all_risk_bands
from croesus.profiles.models import InvestorProfile, PolicyTarget
from croesus.profiles.policy_templates import get_policy_template, instantiate_template

# Korean display labels keyed by band name (risk_return_map.yaml). Ascending risk.
PRESET_LABELS: dict[str, str] = {
    "capital_preservation": "보수 · 자본보전",
    "balanced": "균형 · 중립",
    "growth": "성장 · 적극",
    "equity_max": "공격 · 주식 최대",
}


def _midpoint(span: tuple[float, float]) -> float:
    return (span[0] + span[1]) / 2.0


def list_presets() -> list[RiskBand]:
    """House risk bands, ascending risk, for the preset dropdown."""
    return all_risk_bands()


def preset_label(band_name: str) -> str:
    return PRESET_LABELS.get(band_name, band_name)


def band_by_name(name: str) -> RiskBand | None:
    return next((b for b in all_risk_bands() if b.name == name), None)


def preset_profile(
    band: RiskBand, base: InvestorProfile
) -> tuple[InvestorProfile, list[PolicyTarget]]:
    """Build a profile draft from ``band``, preserving ``base``'s identity.

    Core return/drawdown come from the band's range midpoints, the horizon from
    its recommended minimum, and the diversification caps from its guardrails —
    the same fields the guided flow derives. Everything else (id, name, currency,
    contributions, allowed asset types, trade mode) is kept from ``base`` so a
    preset edits the active profile rather than replacing the user's identity.
    """
    g = band.guardrails
    profile = replace(
        base,
        expected_annual_return=_midpoint(band.expected_return_range),
        max_tolerable_drawdown=_midpoint(band.historical_drawdown_range),
        investment_horizon_years=band.min_recommended_horizon_years,
        liquidity_buffer_months=g.liquidity_buffer_months,
        max_single_position_weight=g.max_single_position_weight,
        max_sector_weight=g.max_sector_weight,
        max_industry_weight=g.max_industry_weight,
        max_theme_weight=g.max_theme_weight,
        max_country_weight=g.max_country_weight,
        max_currency_weight=g.max_currency_weight,
        max_monthly_turnover=g.max_monthly_turnover,
        rebalance_band=g.rebalance_band,
    )
    targets = instantiate_template(get_policy_template(band.template_id), base.profile_id)
    return profile, targets
