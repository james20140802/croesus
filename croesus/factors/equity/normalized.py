"""Normalized-FCF DCF math (reverse-DCF methodology).

Pure and DB-free, mirroring :mod:`croesus.factors.equity.valuation`. Normalizes
the FCF *level* (median, not the latest trough/peak year) and the FCF *growth*
(log-linear regression slope, robust to endpoint artifacts), then powers a
reverse DCF that solves for the growth the market price implies.
"""
from __future__ import annotations

import math
import statistics

from croesus.factors.equity.valuation import FCF_GROWTH_CAP, FCF_GROWTH_FLOOR

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
