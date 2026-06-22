from datetime import date

from croesus.opportunities.review import OpportunityCard, OpportunityReviewResult
from croesus.opportunities.risk_gate import RiskGateVerdict
from croesus.opportunities.selection import OPPORTUNITY_METHODOLOGIES
from croesus.reports.opportunity import render_opportunity_review


def _card(asset_id, verdict):
    return OpportunityCard(
        asset_id=asset_id, symbol=asset_id, name=None,
        methodology_key="moat_adjusted_intrinsic_value", as_of_date=date(2026, 6, 23),
        current_price=400.0, mechanical_intrinsic_value=420.0, mechanical_upside_pct=0.05,
        band_intrinsic_by_scenario={"bear": 300.0, "base": 500.0, "bull": 650.0},
        band_upside_by_scenario={"bear": -0.25, "base": 0.25, "bull": 0.6},
        base_upside_pct=0.25, thesis_as_of_date=date(2026, 6, 23),
        thesis_confidence="medium", evidence_source="filing",
        moat_grade="narrow", tech_grade="parity", sector_grade="stable",
        disruption_grade="medium", moat_evidence="x", tech_evidence="x",
        sector_evidence="x", disruption_evidence="x", bear_case="x",
        risk_gate=verdict,
    )


def test_report_renders_gate_status_and_summary():
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["moat_adjusted_intrinsic_value"],
        as_of_date=date(2026, 6, 23),
        cards=[
            _card("NVDA", RiskGateVerdict("block", ["SECTOR_OVER_MAX"], ["SECTOR_OVER_MAX: ..."])),
            _card("LLY", RiskGateVerdict("pass", [], [])),
        ],
        gate_summary={"pass": 1, "warn": 0, "block": 1},
    )
    out = render_opportunity_review(result)
    assert "Risk gate: 1 pass / 0 warn / 1 block" in out
    assert "Risk gate: BLOCK [SECTOR_OVER_MAX]" in out
    assert "Risk gate: PASS" in out


def test_report_renders_dash_when_gate_absent():
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["moat_adjusted_intrinsic_value"],
        as_of_date=date(2026, 6, 23),
        cards=[_card("LLY", None)],
        gate_summary=None,
    )
    out = render_opportunity_review(result)
    assert "Risk gate: —" in out
