"""Return-anchored profile guidance (Sprint 003c).

A deterministic layer that turns a single stated preference — a desired return
or an acceptable drawdown — into a consistent set of implied profile fields, by
reading the editable mapping table in ``risk_return_map.yaml``. It detects when a
stated return and a stated drawdown are incompatible and offers concrete
resolution options, and translates abstract percentages into currency amounts
and historical episodes.

Every number originates in the YAML. No value is invented here, and nothing in
this module calls an LLM. The resulting draft is advisory: callers must still run
``validate_profile`` (Sprint 003), which remains the only gate.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from croesus.profiles.models import InvestorProfile

_MAP_PATH = Path(__file__).with_name("risk_return_map.yaml")

ABOVE_HIGHEST = "above_highest"
"""Sentinel ``matched_band`` value for return targets above the highest band."""


@dataclass(frozen=True)
class RiskBand:
    name: str
    expected_return_range: tuple[float, float]
    typical_equity_weight: tuple[float, float]
    historical_drawdown_range: tuple[float, float]
    min_recommended_horizon_years: int
    template_id: str


@dataclass(frozen=True)
class HistoricalEpisode:
    year: int
    label: str
    drawdown_by_band: dict[str, float]


@dataclass(frozen=True)
class ScenarioLine:
    episode_year: int
    episode_label: str
    band_name: str
    approximate_drawdown: float
    currency_amount: float | None
    currency: str | None


@dataclass(frozen=True)
class ResolutionOption:
    key: str  # "keep_return" | "keep_drawdown" | "meet_in_middle"
    description: str
    implied_return_range: tuple[float, float]
    implied_drawdown_range: tuple[float, float]
    implied_min_horizon_years: int
    template_id: str


@dataclass(frozen=True)
class GuidanceConflict:
    field_a: str
    field_b: str
    description: str
    options: list[ResolutionOption]


@dataclass(frozen=True)
class ProfileGuidance:
    anchor: str  # "return" | "drawdown"
    matched_band: str
    implied_drawdown_range: tuple[float, float] | None
    implied_return_range: tuple[float, float] | None
    min_recommended_horizon_years: int
    template_id: str
    scenarios: list[ScenarioLine]
    conflicts: list[GuidanceConflict]
    warnings: list[str]


def _load_map() -> tuple[list[RiskBand], list[HistoricalEpisode]]:
    raw = yaml.safe_load(_MAP_PATH.read_text(encoding="utf-8"))
    bands = [
        RiskBand(
            name=b["name"],
            expected_return_range=tuple(b["expected_return_range"]),
            typical_equity_weight=tuple(b["typical_equity_weight"]),
            historical_drawdown_range=tuple(b["historical_drawdown_range"]),
            min_recommended_horizon_years=int(b["min_recommended_horizon_years"]),
            template_id=b["template_id"],
        )
        for b in raw["bands"]
    ]
    episodes = [
        HistoricalEpisode(
            year=int(e["year"]),
            label=e["label"],
            drawdown_by_band=dict(e["drawdown_by_band"]),
        )
        for e in raw["historical_episodes"]
    ]
    return bands, episodes


# Loaded once at import, mirroring POLICY_TEMPLATES. Tests patch these in place.
_BANDS, _EPISODES = _load_map()


def _find_band_for_return(value: float) -> RiskBand | None:
    """Return the band whose expected_return_range contains ``value``.

    Bands are non-overlapping and ascending. The highest band's upper bound is
    treated as inclusive so a value exactly on it stays in-band. Returns ``None``
    when the value is above the highest band.
    """
    for i, band in enumerate(_BANDS):
        lo, hi = band.expected_return_range
        is_last = i == len(_BANDS) - 1
        if lo <= value < hi or (is_last and value == hi):
            return band
    return None


def _find_band_for_drawdown(value: float) -> RiskBand | None:
    """Return the band whose historical_drawdown_range contains ``value``.

    ``value`` is negative (e.g. -0.20). drawdown ranges are ``[worse, milder]``
    with both negative. The deepest band treats anything at or below its worse
    bound as in-band; the shallowest treats anything at or above its milder bound
    as in-band, so the whole number line maps to some band.
    """
    for i, band in enumerate(_BANDS):
        worse, milder = band.historical_drawdown_range
        is_first = i == 0
        is_last = i == len(_BANDS) - 1
        if worse <= value <= milder:
            return band
        if is_first and value >= milder:
            return band
        if is_last and value <= worse:
            return band
    return None


def _balanced_band() -> RiskBand:
    """Band used for the meet-in-the-middle resolution (the 'balanced' band)."""
    for band in _BANDS:
        if band.name == "balanced":
            return band
    return _BANDS[len(_BANDS) // 2]


def _build_scenarios(
    band_name: str,
    portfolio_size: float | None,
    currency: str | None,
) -> list[ScenarioLine]:
    lines: list[ScenarioLine] = []
    for ep in _EPISODES:
        dd = ep.drawdown_by_band.get(band_name)
        if dd is None:
            continue
        amount = portfolio_size * dd if portfolio_size is not None else None
        lines.append(
            ScenarioLine(
                episode_year=ep.year,
                episode_label=ep.label,
                band_name=band_name,
                approximate_drawdown=dd,
                currency_amount=amount,
                currency=currency,
            )
        )
    return lines


def _band_guidance(
    band: RiskBand,
    *,
    anchor: str,
    portfolio_size: float | None,
    portfolio_currency: str | None,
    conflicts: list[GuidanceConflict] | None = None,
    warnings: list[str] | None = None,
) -> ProfileGuidance:
    return ProfileGuidance(
        anchor=anchor,
        matched_band=band.name,
        implied_drawdown_range=band.historical_drawdown_range,
        implied_return_range=band.expected_return_range,
        min_recommended_horizon_years=band.min_recommended_horizon_years,
        template_id=band.template_id,
        scenarios=_build_scenarios(band.name, portfolio_size, portfolio_currency),
        conflicts=conflicts or [],
        warnings=warnings or [],
    )


def _above_highest_guidance(value: float, anchor: str) -> ProfileGuidance:
    top = _BANDS[-1]
    warning = (
        f"A target return of {value:.1%} is above the highest configured band "
        f"({top.expected_return_range[0]:.1%}–{top.expected_return_range[1]:.1%}). "
        "Diversified public-market portfolios have not historically delivered "
        "returns at this level. No allocation recommendation is made — reaching "
        "above-band returns would require leverage or concentration, which this "
        "guidance does not propose."
    )
    return ProfileGuidance(
        anchor=anchor,
        matched_band=ABOVE_HIGHEST,
        implied_drawdown_range=None,
        implied_return_range=None,
        min_recommended_horizon_years=0,
        template_id="",
        scenarios=[],
        conflicts=[],
        warnings=[warning],
    )


def anchor_on_return(
    value: float,
    *,
    portfolio_size: float | None = None,
    portfolio_currency: str | None = None,
) -> ProfileGuidance:
    """Derive guidance from a desired annual return (e.g. ``0.08``)."""
    band = _find_band_for_return(value)
    if band is None:
        return _above_highest_guidance(value, anchor="return")
    return _band_guidance(
        band,
        anchor="return",
        portfolio_size=portfolio_size,
        portfolio_currency=portfolio_currency,
    )


def anchor_on_drawdown(
    value: float,
    *,
    portfolio_size: float | None = None,
    portfolio_currency: str | None = None,
) -> ProfileGuidance:
    """Derive guidance from a drawdown tolerance (negative, e.g. ``-0.20``).

    ``implied_return_range`` is the band's realistic return range — the ceiling
    a portfolio at that drawdown level has historically supported.
    """
    band = _find_band_for_drawdown(value)
    if band is None:  # defensive; the lookup covers the whole line
        return _band_guidance(
            _BANDS[0],
            anchor="drawdown",
            portfolio_size=portfolio_size,
            portfolio_currency=portfolio_currency,
        )
    return _band_guidance(
        band,
        anchor="drawdown",
        portfolio_size=portfolio_size,
        portfolio_currency=portfolio_currency,
    )


def _resolution_option(key: str, description: str, band: RiskBand) -> ResolutionOption:
    return ResolutionOption(
        key=key,
        description=description,
        implied_return_range=band.expected_return_range,
        implied_drawdown_range=band.historical_drawdown_range,
        implied_min_horizon_years=band.min_recommended_horizon_years,
        template_id=band.template_id,
    )


def _build_conflict(
    return_val: float,
    return_band: RiskBand,
    drawdown_val: float,
    drawdown_band: RiskBand,
) -> GuidanceConflict:
    mid = _balanced_band()
    keep_return = _resolution_option(
        "keep_return",
        (
            f"Keep the {return_val:.1%} return target. Accept historical drawdown "
            f"around {return_band.historical_drawdown_range[0]:.0%} to "
            f"{return_band.historical_drawdown_range[1]:.0%} and a horizon of at "
            f"least {return_band.min_recommended_horizon_years} years."
        ),
        return_band,
    )
    keep_drawdown = _resolution_option(
        "keep_drawdown",
        (
            f"Keep the {drawdown_val:.0%} drawdown tolerance. Lower the expected "
            f"return to {drawdown_band.expected_return_range[0]:.1%}–"
            f"{drawdown_band.expected_return_range[1]:.1%}."
        ),
        drawdown_band,
    )
    meet_in_middle = _resolution_option(
        "meet_in_middle",
        (
            f"Balanced compromise: return {mid.expected_return_range[0]:.1%}–"
            f"{mid.expected_return_range[1]:.1%}, drawdown "
            f"{mid.historical_drawdown_range[0]:.0%}–"
            f"{mid.historical_drawdown_range[1]:.0%}, horizon at least "
            f"{mid.min_recommended_horizon_years} years."
        ),
        mid,
    )
    return GuidanceConflict(
        field_a="expected_annual_return",
        field_b="max_tolerable_drawdown",
        description=(
            f"A {return_val:.1%} return implies band '{return_band.name}' "
            f"(drawdown {return_band.historical_drawdown_range[0]:.0%}–"
            f"{return_band.historical_drawdown_range[1]:.0%}), but a "
            f"{drawdown_val:.0%} drawdown tolerance implies band "
            f"'{drawdown_band.name}'. These are inconsistent — choose how to "
            "resolve them below."
        ),
        options=[keep_return, keep_drawdown, meet_in_middle],
    )


def detect_conflict(
    return_val: float,
    drawdown_val: float,
    *,
    portfolio_size: float | None = None,
    portfolio_currency: float | None = None,
) -> ProfileGuidance:
    """Compare a stated return and drawdown; attach resolution options on clash.

    If the return is above the highest band, the above-band guidance (with its
    warning) is returned. If both values fall in the same band, no conflict is
    raised. Otherwise the returned guidance (anchored on the return band) carries
    a single :class:`GuidanceConflict` with three resolution options.
    """
    return_band = _find_band_for_return(return_val)
    if return_band is None:
        return _above_highest_guidance(return_val, anchor="return")

    drawdown_band = _find_band_for_drawdown(drawdown_val)
    if drawdown_band is not None and drawdown_band.name == return_band.name:
        return _band_guidance(
            return_band,
            anchor="return",
            portfolio_size=portfolio_size,
            portfolio_currency=portfolio_currency,
        )

    # Fall back to the return band as the anchor when a drawdown band is somehow
    # unresolved (the lookup covers the whole line, so this is defensive).
    resolved_drawdown_band = drawdown_band or return_band
    conflict = _build_conflict(
        return_val, return_band, drawdown_val, resolved_drawdown_band
    )
    return _band_guidance(
        return_band,
        anchor="return",
        portfolio_size=portfolio_size,
        portfolio_currency=portfolio_currency,
        conflicts=[conflict],
    )


def _midpoint(span: tuple[float, float]) -> float:
    return (span[0] + span[1]) / 2.0


def apply_guidance_to_profile(
    base_profile: InvestorProfile,
    guidance: ProfileGuidance,
) -> InvestorProfile:
    """Return a copy of ``base_profile`` with guidance-derived fields applied.

    Only ``expected_annual_return`` (return-range midpoint),
    ``max_tolerable_drawdown`` (drawdown-range midpoint, negative), and
    ``investment_horizon_years`` (the minimum recommended horizon) are set; every
    other field — including ``profile_id`` — is preserved. When the guidance has
    no usable bands (above-highest), ``base_profile`` is returned unchanged.

    The result is a draft only; the caller must still run ``validate_profile``.
    """
    if (
        guidance.implied_return_range is None
        or guidance.implied_drawdown_range is None
    ):
        return base_profile
    return replace(
        base_profile,
        expected_annual_return=_midpoint(guidance.implied_return_range),
        max_tolerable_drawdown=_midpoint(guidance.implied_drawdown_range),
        investment_horizon_years=guidance.min_recommended_horizon_years,
    )


def apply_resolution_to_profile(
    base_profile: InvestorProfile,
    option: ResolutionOption,
) -> InvestorProfile:
    """Apply a chosen :class:`ResolutionOption` to a profile, like the above."""
    return replace(
        base_profile,
        expected_annual_return=_midpoint(option.implied_return_range),
        max_tolerable_drawdown=_midpoint(option.implied_drawdown_range),
        investment_horizon_years=option.implied_min_horizon_years,
    )
