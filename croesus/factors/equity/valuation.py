"""
Pure valuation math (Sprint 007).

DB-free and deterministic: relative-valuation multiples, sector-percentile
ranking, CAPM WACC, FCF growth estimation, and a 2-stage DCF. The orchestration
layer (``compute_valuation.py``) reads the database, calls these functions, and
persists the results. Keeping the math pure makes every formula unit-testable
without a database or network.
"""
from __future__ import annotations

from dataclasses import dataclass

# CAPM / DCF constants (spec §4).
EQUITY_RISK_PREMIUM = 0.055
DEFAULT_RISK_FREE_RATE = 0.045
DEFAULT_TERMINAL_GROWTH = 0.025
FCF_GROWTH_FLOOR = -0.05
FCF_GROWTH_CAP = 0.30
DCF_EXPLICIT_YEARS = 5
MIN_BETA_OBSERVATIONS = 30


@dataclass(frozen=True)
class DcfKnobs:
    """Forward-looking DCF assumptions a thesis may later revise.

    Phase A exposes them as a named, overridable bundle; Phase C drives them
    from structural-thesis grades (moat → CAP, sector → terminal, disruption →
    risk premium). The defaults reproduce the pre-Phase-A behavior exactly.
    """

    explicit_years: int = DCF_EXPLICIT_YEARS          # competitive-advantage period (CAP)
    terminal_growth_rate: float = DEFAULT_TERMINAL_GROWTH
    wacc_risk_premium: float = 0.0                    # added to the CAPM WACC


DEFAULT_DCF_KNOBS = DcfKnobs()


@dataclass(frozen=True)
class ValuationMultiples:
    pe_ratio: float | None = None
    pb_ratio: float | None = None
    ev_to_ebitda: float | None = None
    fcf_yield: float | None = None


@dataclass(frozen=True)
class DcfResult:
    intrinsic_value_per_share: float
    wacc: float
    fcf_growth_rate: float
    terminal_growth_rate: float
    enterprise_value: float
    equity_value: float
    base_fcf: float


def compute_multiples(
    *,
    price: float | None,
    eps: float | None,
    book_value_per_share: float | None,
    market_cap: float | None,
    total_debt: float | None,
    cash: float | None,
    ebitda: float | None,
    free_cash_flow: float | None,
) -> ValuationMultiples:
    """Relative-valuation multiples; any factor with a 0/None/negative
    denominator is left ``None`` (a negative multiple would corrupt the
    cheap→expensive percentile ordering)."""
    return ValuationMultiples(
        pe_ratio=_ratio(price, eps),
        pb_ratio=_ratio(price, book_value_per_share),
        ev_to_ebitda=_enterprise_value_to_ebitda(market_cap, total_debt, cash, ebitda),
        fcf_yield=_fcf_yield(free_cash_flow, market_cap),
    )


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def _enterprise_value_to_ebitda(
    market_cap: float | None,
    total_debt: float | None,
    cash: float | None,
    ebitda: float | None,
) -> float | None:
    if market_cap is None or ebitda is None or ebitda <= 0:
        return None
    enterprise_value = market_cap + (total_debt or 0.0) - (cash or 0.0)
    return enterprise_value / ebitda


def _fcf_yield(free_cash_flow: float | None, market_cap: float | None) -> float | None:
    # market_cap is the denominator (always > 0 for a live name); FCF may be
    # negative, which is a meaningful (expensive) reading, so it is kept.
    if free_cash_flow is None or market_cap is None or market_cap <= 0:
        return None
    return free_cash_flow / market_cap


def sector_percentile(value: float, peers: list[float]) -> float | None:
    """Mid-rank percentile of ``value`` within ``peers`` (which includes itself),
    on a 0–100 ascending scale: 0 = cheapest (lowest multiple), 100 = priciest."""
    usable = [p for p in peers if p is not None]
    if not usable:
        return None
    below = sum(1 for p in usable if p < value)
    equal = sum(1 for p in usable if p == value)
    return (below + 0.5 * equal) / len(usable) * 100.0


def compute_beta(
    asset_returns: list[float], market_returns: list[float]
) -> float | None:
    """OLS slope of asset returns on market returns (``cov/var``).

    Returns ``None`` when the overlapping series is too short or the market has
    zero variance — the caller then falls back to a sector median, then 1.0.
    """
    n = min(len(asset_returns), len(market_returns))
    if n < MIN_BETA_OBSERVATIONS:
        return None
    a = asset_returns[-n:]
    m = market_returns[-n:]
    mean_a = sum(a) / n
    mean_m = sum(m) / n
    cov = sum((ai - mean_a) * (mi - mean_m) for ai, mi in zip(a, m))
    var = sum((mi - mean_m) ** 2 for mi in m)
    if var == 0:
        return None
    return cov / var


def compute_wacc(
    risk_free_rate: float,
    beta: float,
    *,
    equity_risk_premium: float = EQUITY_RISK_PREMIUM,
    risk_premium: float = 0.0,
) -> float:
    """All-equity CAPM cost of capital: ``Rf + β × ERP + risk_premium``.

    ``risk_premium`` is a thesis-driven add-on (e.g. disruption risk); it
    defaults to 0.0 so existing callers are unchanged. Debt-weighting remains
    out of scope.
    """
    return risk_free_rate + beta * equity_risk_premium + risk_premium


def compute_fcf_growth(annual_fcf: list[float]) -> float | None:
    """5-year FCF CAGR clipped to ``[-5%, +30%]``.

    Uses up to the most recent five annual figures. Returns ``None`` when there
    are fewer than two points or either endpoint is non-positive (a CAGR across a
    sign change is undefined).
    """
    # NOTE: this look-back window is pinned to the module constant, not
    # knobs.explicit_years. Identical today (DEFAULT_DCF_KNOBS.explicit_years ==
    # DCF_EXPLICIT_YEARS). Phase C, which lets a thesis stretch the CAP, must
    # decide whether the CAGR window should track that or stay a fixed history.
    recent = annual_fcf[-DCF_EXPLICIT_YEARS:]
    if len(recent) < 2:
        return None
    first, last = recent[0], recent[-1]
    if first <= 0 or last <= 0:
        return None
    years = len(recent) - 1
    cagr = (last / first) ** (1 / years) - 1
    return max(FCF_GROWTH_FLOOR, min(FCF_GROWTH_CAP, cagr))


def two_stage_dcf(
    *,
    base_fcf: float,
    growth_rate: float,
    wacc: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    knobs: DcfKnobs = DEFAULT_DCF_KNOBS,
) -> DcfResult | None:
    """Explicit FCF projection over the competitive-advantage period + Gordon
    terminal value. The projection horizon and terminal growth come from
    ``knobs`` (defaults reproduce the prior 5-year / 2.5% behavior).

    Returns ``None`` (DCF skipped) when inputs make the model invalid: a
    non-positive FCF base, no shares, or ``WACC ≤ terminal growth``.
    """
    terminal_growth_rate = knobs.terminal_growth_rate
    explicit_years = knobs.explicit_years
    if base_fcf <= 0 or shares_outstanding <= 0:
        return None
    if wacc <= terminal_growth_rate:
        return None

    pv_explicit = 0.0
    projected_fcf = base_fcf
    for year in range(1, explicit_years + 1):
        projected_fcf = base_fcf * (1 + growth_rate) ** year
        pv_explicit += projected_fcf / (1 + wacc) ** year

    fcf_final = base_fcf * (1 + growth_rate) ** explicit_years
    terminal_value = fcf_final * (1 + terminal_growth_rate) / (wacc - terminal_growth_rate)
    pv_terminal = terminal_value / (1 + wacc) ** explicit_years

    enterprise_value = pv_explicit + pv_terminal
    equity_value = enterprise_value - (total_debt or 0.0) + (cash or 0.0)
    intrinsic_per_share = equity_value / shares_outstanding

    return DcfResult(
        intrinsic_value_per_share=intrinsic_per_share,
        wacc=wacc,
        fcf_growth_rate=growth_rate,
        terminal_growth_rate=terminal_growth_rate,
        enterprise_value=enterprise_value,
        equity_value=equity_value,
        base_fcf=base_fcf,
    )


def value_with_knobs(
    *,
    base_fcf: float,
    growth_rate: float,
    risk_free_rate: float,
    beta: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    knobs: DcfKnobs = DEFAULT_DCF_KNOBS,
) -> DcfResult | None:
    """Single recompute path: derive WACC (with the knob bundle's
    ``wacc_risk_premium``) and run the two-stage DCF under one ``knobs`` set.
    Callers (the valuation job today, a thesis-revision step later) recompute
    by passing a different ``knobs``.
    """
    wacc = compute_wacc(risk_free_rate, beta, risk_premium=knobs.wacc_risk_premium)
    return two_stage_dcf(
        base_fcf=base_fcf,
        growth_rate=growth_rate,
        wacc=wacc,
        shares_outstanding=shares_outstanding,
        total_debt=total_debt,
        cash=cash,
        knobs=knobs,
    )
