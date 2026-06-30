"""Normalized-FCF DCF math (reverse-DCF methodology).

Pure and DB-free, mirroring :mod:`croesus.factors.equity.valuation`. Normalizes
the FCF *level* (median, not the latest trough/peak year) and the FCF *growth*
(log-linear regression slope, robust to endpoint artifacts), then powers a
reverse DCF that solves for the growth the market price implies.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from croesus.factors.equity.valuation import (
    DEFAULT_DCF_KNOBS,
    DcfKnobs,
    FCF_GROWTH_CAP,
    FCF_GROWTH_FLOOR,
    two_stage_dcf,
)

NORMALIZED_FCF_WINDOW = 10        # years of FCF history to normalize over
MIN_NORMALIZED_FCF_YEARS = 4      # fewer available -> "short_history" flag
MIN_POSITIVE_FCF_POINTS = 2       # fewer positive points -> growth undefined


def normalized_base_fcf(
    annual_fcf: list[float], *, window: int = NORMALIZED_FCF_WINDOW
) -> float | None:
    """Median of the most recent ``window`` annual FCF values (``None`` if empty).

    Median (vs the latest year) damps a single peak/trough year — the endpoint
    artifact that makes a flat compounder look like a decliner.
    """
    recent = annual_fcf[-window:]
    if not recent:
        return None
    return statistics.median(recent)


def loglinear_fcf_growth(
    annual_fcf: list[float], *, window: int = NORMALIZED_FCF_WINDOW
) -> float | None:
    """Annualized growth = ``exp(OLS slope of ln(FCF) on year index) - 1``.

    Uses only positive points within the most recent ``window`` years, keeping
    their original index spacing so gaps from skipped (non-positive) years are
    preserved. ``None`` when fewer than ``MIN_POSITIVE_FCF_POINTS`` positive
    points exist (growth across a sign change is undefined). Clipped to the same
    ``[FCF_GROWTH_FLOOR, FCF_GROWTH_CAP]`` band as the mechanical model.
    """
    recent = annual_fcf[-window:]
    points = [(i, v) for i, v in enumerate(recent) if v > 0]
    if len(points) < MIN_POSITIVE_FCF_POINTS:
        return None
    xs = [i for i, _ in points]
    ys = [math.log(v) for _, v in points]
    n = len(xs)
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx == 0:
        return None
    growth = math.exp(sxy / sxx) - 1.0
    return max(FCF_GROWTH_FLOOR, min(FCF_GROWTH_CAP, growth))


def reverse_dcf_implied_growth(
    *,
    price: float,
    base_fcf: float,
    wacc: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    knobs: DcfKnobs = DEFAULT_DCF_KNOBS,
    lo: float = -0.50,
    hi: float = 1.00,
    iterations: int = 100,
) -> float | None:
    """FCF growth ``g`` such that the two-stage DCF intrinsic equals ``price``.

    Intrinsic is monotonically increasing in ``g``, so we bracket-check then
    bisect on ``[lo, hi]``. ``None`` when inputs are invalid (``base_fcf <= 0``,
    no shares, ``wacc <= terminal``) or the price is not bracketed within the
    search range (i.e. implied growth is outside ``[lo, hi]`` — e.g. a name
    priced for >100% growth).
    """
    if base_fcf <= 0 or shares_outstanding <= 0 or wacc <= knobs.terminal_growth_rate:
        return None

    def intrinsic(g: float) -> float | None:
        result = two_stage_dcf(
            base_fcf=base_fcf, growth_rate=g, wacc=wacc,
            shares_outstanding=shares_outstanding,
            total_debt=total_debt, cash=cash, knobs=knobs,
        )
        return result.intrinsic_value_per_share if result else None

    low_v, high_v = intrinsic(lo), intrinsic(hi)
    if low_v is None or high_v is None or not (low_v <= price <= high_v):
        return None

    for _ in range(iterations):
        mid = (lo + hi) / 2
        v = intrinsic(mid)
        if v is None:
            return None
        if v < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


QUALITY_OK = "ok"
QUALITY_SHORT_HISTORY = "short_history"
QUALITY_FCF_NOT_MEANINGFUL = "fcf_not_meaningful"
# reference_growth saturated at a clip boundary (>= CAP or <= FLOOR): the
# log-linear anchor is pinned, so the plausibility gap is not trustworthy.
QUALITY_REFERENCE_UNRELIABLE = "reference_unreliable"


@dataclass(frozen=True)
class NormalizedDcfResult:
    normalized_base_fcf: float | None
    reference_growth: float | None
    normalized_intrinsic_value_per_share: float | None
    normalized_upside_pct: float | None
    implied_growth: float | None
    plausibility_gap: float | None
    valuation_quality: str
    n_fcf_years: int


def evaluate_normalized_dcf(
    *,
    annual_fcf: list[float],
    price: float,
    wacc: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    knobs: DcfKnobs = DEFAULT_DCF_KNOBS,
    window: int = NORMALIZED_FCF_WINDOW,
    min_years: int = MIN_NORMALIZED_FCF_YEARS,
) -> NormalizedDcfResult:
    """One-shot normalized DCF: median base + log-linear reference growth +
    normalized forward intrinsic + reverse-DCF implied growth + plausibility gap.

    ``valuation_quality`` is ``fcf_not_meaningful`` when the normalized base or
    reference growth is undefined (sign-flipping FCF), else ``reference_unreliable``
    when reference growth is pinned at a clip boundary (the gap anchor is
    saturated), else ``short_history`` when fewer than ``min_years`` of FCF are
    available, else ``ok``. Sector-level exclusions (e.g. financials) are applied
    by the orchestration layer, not here. Returns a fully-populated result in
    every case (never raises).
    """
    n_years = len(annual_fcf[-window:])
    base = normalized_base_fcf(annual_fcf, window=window)
    growth = loglinear_fcf_growth(annual_fcf, window=window)
    if base is None or base <= 0 or growth is None:
        return NormalizedDcfResult(
            normalized_base_fcf=base, reference_growth=growth,
            normalized_intrinsic_value_per_share=None, normalized_upside_pct=None,
            implied_growth=None, plausibility_gap=None,
            valuation_quality=QUALITY_FCF_NOT_MEANINGFUL, n_fcf_years=n_years,
        )
    forward = two_stage_dcf(
        base_fcf=base, growth_rate=growth, wacc=wacc,
        shares_outstanding=shares_outstanding, total_debt=total_debt, cash=cash,
        knobs=knobs,
    )
    intrinsic = forward.intrinsic_value_per_share if forward else None
    upside = (intrinsic / price - 1.0) if (intrinsic is not None and price) else None
    implied = reverse_dcf_implied_growth(
        price=price, base_fcf=base, wacc=wacc,
        shares_outstanding=shares_outstanding, total_debt=total_debt, cash=cash,
        knobs=knobs,
    )
    gap = (implied - growth) if implied is not None else None
    if growth <= FCF_GROWTH_FLOOR or growth >= FCF_GROWTH_CAP:
        # Anchor pinned at a clip boundary -> gap is unreliable; deprioritized
        # (still persisted so the breakdown is visible).
        quality = QUALITY_REFERENCE_UNRELIABLE
    elif n_years < min_years:
        quality = QUALITY_SHORT_HISTORY
    else:
        quality = QUALITY_OK
    return NormalizedDcfResult(
        normalized_base_fcf=base, reference_growth=growth,
        normalized_intrinsic_value_per_share=intrinsic, normalized_upside_pct=upside,
        implied_growth=implied, plausibility_gap=gap,
        valuation_quality=quality, n_fcf_years=n_years,
    )
