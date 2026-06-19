from __future__ import annotations

from croesus.factors.equity.valuation import DcfKnobs

# Spec §방법론 A mapping tables (keys are the C2 grade vocabularies).
CAP_YEARS = {"wide": 10, "narrow": 7, "none": 5}
TERMINAL_GROWTH = {"secular_growth": 0.030, "stable": 0.025, "declining": 0.015}
RISK_PREMIUM = {"low": 0.00, "medium": 0.01, "high": 0.02}

# Ordered worst->best for moat/sector (best = more CAP / higher terminal growth).
_MOAT_ORDER = ("none", "narrow", "wide")
_SECTOR_ORDER = ("declining", "stable", "secular_growth")
# Ordered low->high RISK for disruption (more risk = higher premium = worse).
_DISRUPTION_ORDER = ("low", "medium", "high")

# Default level per dimension when no grade is present — reproduces DEFAULT_DCF_KNOBS.
_DEFAULT_MOAT = "none"
_DEFAULT_SECTOR = "stable"
_DEFAULT_DISRUPTION = "low"

# Scenario step: bear pessimistic, bull optimistic (in moat/sector index terms).
_STEP = {"bear": -1, "base": 0, "bull": +1}


def _step(order: tuple[str, ...], level: str | None, delta: int, default: str) -> str:
    """Move ``level`` ``delta`` positions along ``order``, clamped to its ends."""
    current = level if level in order else default
    idx = order.index(current)
    clamped = max(0, min(len(order) - 1, idx + delta))
    return order[clamped]


def scenario_knobs(
    *, moat: str | None, sector: str | None, disruption: str | None, scenario: str
) -> DcfKnobs:
    """Map a thesis grade to a scenario's DCF knobs.

    base = grade-mapped knobs; bear/bull step every dimension one notch toward
    pessimism/optimism (disruption inverted: pessimism = more risk premium),
    clamped to the grade vocabulary.
    """
    delta = _STEP[scenario]
    moat_lvl = _step(_MOAT_ORDER, moat, delta, _DEFAULT_MOAT)
    sector_lvl = _step(_SECTOR_ORDER, sector, delta, _DEFAULT_SECTOR)
    # Optimism = LESS disruption risk, so step disruption opposite to delta.
    disruption_lvl = _step(_DISRUPTION_ORDER, disruption, -delta, _DEFAULT_DISRUPTION)
    return DcfKnobs(
        explicit_years=CAP_YEARS[moat_lvl],
        terminal_growth_rate=TERMINAL_GROWTH[sector_lvl],
        wacc_risk_premium=RISK_PREMIUM[disruption_lvl],
    )
