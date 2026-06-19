from __future__ import annotations

from dataclasses import dataclass

from croesus.factors.equity.thesis_knobs import scenario_knobs
from croesus.factors.equity.valuation import value_with_knobs

SCENARIOS = ("bear", "base", "bull")


@dataclass(frozen=True)
class ScenarioBand:
    scenario: str
    intrinsic_value_per_share: float
    wacc: float
    fcf_growth_rate: float
    terminal_growth_rate: float
    explicit_years: int
    wacc_risk_premium: float


def compute_intrinsic_bands(
    *,
    base_fcf: float,
    growth: float,
    risk_free_rate: float,
    beta: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    moat: str | None,
    sector: str | None,
    disruption: str | None,
) -> dict[str, ScenarioBand | None]:
    """Value bear/base/bull scenarios from one thesis grade.

    Growth is shared across scenarios (an observed fact); scenarios differ only
    in CAP / terminal growth / risk premium via ``scenario_knobs``. A scenario is
    ``None`` when its knobs make the DCF invalid (e.g. WACC <= terminal growth).
    """
    bands: dict[str, ScenarioBand | None] = {}
    for scenario in SCENARIOS:
        knobs = scenario_knobs(
            moat=moat, sector=sector, disruption=disruption, scenario=scenario
        )
        dcf = value_with_knobs(
            base_fcf=base_fcf,
            growth_rate=growth,
            risk_free_rate=risk_free_rate,
            beta=beta,
            shares_outstanding=shares_outstanding,
            total_debt=total_debt,
            cash=cash,
            knobs=knobs,
        )
        bands[scenario] = (
            None
            if dcf is None
            else ScenarioBand(
                scenario=scenario,
                intrinsic_value_per_share=dcf.intrinsic_value_per_share,
                wacc=dcf.wacc,
                fcf_growth_rate=dcf.fcf_growth_rate,
                terminal_growth_rate=dcf.terminal_growth_rate,
                explicit_years=knobs.explicit_years,
                wacc_risk_premium=knobs.wacc_risk_premium,
            )
        )
    return bands
