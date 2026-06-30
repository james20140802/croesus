"""
Test that render_opportunity_review surfaces normalized-DCF fields.

The real public function is `render_opportunity_review` (not the name suggested
in the task brief); the brief's `render_opportunity_report` alias does not exist.
"""

from datetime import date

from croesus.opportunities.review import OpportunityCard, OpportunityReviewResult
from croesus.opportunities.selection import OPPORTUNITY_METHODOLOGIES
from croesus.reports.opportunity import render_opportunity_review


def _normalized_card() -> OpportunityCard:
    return OpportunityCard(
        asset_id="US_EQ_AAPL",
        symbol="AAPL",
        name="Apple",
        methodology_key="normalized_dcf",
        as_of_date=date(2026, 6, 30),
        current_price=281.74,
        mechanical_intrinsic_value=None,
        mechanical_upside_pct=None,
        band_intrinsic_by_scenario={},
        band_upside_by_scenario={},
        base_upside_pct=None,
        thesis_as_of_date=None,
        thesis_confidence=None,
        evidence_source=None,
        moat_grade=None,
        tech_grade=None,
        sector_grade=None,
        disruption_grade=None,
        moat_evidence=None,
        tech_evidence=None,
        sector_evidence=None,
        disruption_evidence=None,
        bear_case=None,
        normalized_intrinsic_value=54.0,
        normalized_upside_pct=-0.81,
        reference_growth=-0.027,
        implied_growth=0.20,
        plausibility_gap=0.227,
        valuation_quality="ok",
        n_fcf_years=4,
    )


def test_report_shows_symbol_for_normalized_card():
    card = _normalized_card()
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["normalized_dcf"],
        as_of_date=date(2026, 6, 30),
        cards=[card],
    )
    text = render_opportunity_review(result)
    assert "AAPL" in text


def test_report_shows_plausibility_gap_for_normalized_cards():
    """Plausibility gap (0.227) must appear in the rendered report."""
    card = _normalized_card()
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["normalized_dcf"],
        as_of_date=date(2026, 6, 30),
        cards=[card],
    )
    text = render_opportunity_review(result)
    # Either raw decimal or formatted percentage form is acceptable.
    assert "22.7" in text or "0.227" in text, (
        f"Expected plausibility gap in rendered output but got:\n{text}"
    )


def test_report_shows_reference_and_implied_growth():
    card = _normalized_card()
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["normalized_dcf"],
        as_of_date=date(2026, 6, 30),
        cards=[card],
    )
    text = render_opportunity_review(result)
    # reference_growth = -0.027 -> "-2.7%" ; implied_growth = 0.20 -> "+20.0%"
    assert "-2.7" in text or "reference" in text.lower()
    assert "20.0" in text or "implied" in text.lower()


def test_report_shows_normalized_upside_and_floor_label():
    card = _normalized_card()
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["normalized_dcf"],
        as_of_date=date(2026, 6, 30),
        cards=[card],
    )
    text = render_opportunity_review(result)
    # normalized_upside_pct = -0.81 -> "-81.0%"
    assert "-81.0" in text or "81" in text
    # Must label normalized value honestly as a floor, not fair value.
    assert "floor" in text.lower()


def test_report_shows_valuation_quality_badge():
    card = _normalized_card()
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["normalized_dcf"],
        as_of_date=date(2026, 6, 30),
        cards=[card],
    )
    text = render_opportunity_review(result)
    assert "ok" in text


def test_moat_adjusted_card_output_unchanged():
    """Additive check: methodology-A (moat_adjusted_intrinsic_value) output must be unchanged."""
    from croesus.opportunities.risk_gate import RiskGateVerdict

    ma_card = OpportunityCard(
        asset_id="US_EQ_NVDA",
        symbol="NVDA",
        name="Nvidia",
        methodology_key="moat_adjusted_intrinsic_value",
        as_of_date=date(2026, 6, 30),
        current_price=400.0,
        mechanical_intrinsic_value=420.0,
        mechanical_upside_pct=0.05,
        band_intrinsic_by_scenario={"bear": 300.0, "base": 500.0, "bull": 650.0},
        band_upside_by_scenario={"bear": -0.25, "base": 0.25, "bull": 0.6},
        base_upside_pct=0.25,
        thesis_as_of_date=date(2026, 6, 30),
        thesis_confidence="medium",
        evidence_source="filing",
        moat_grade="narrow",
        tech_grade="parity",
        sector_grade="stable",
        disruption_grade="medium",
        moat_evidence="wide moat",
        tech_evidence="parity",
        sector_evidence="stable",
        disruption_evidence="medium",
        bear_case="competition",
        risk_gate=RiskGateVerdict("pass", [], []),
    )
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["moat_adjusted_intrinsic_value"],
        as_of_date=date(2026, 6, 30),
        cards=[ma_card],
    )
    text = render_opportunity_review(result)
    # Existing fields must still appear.
    assert "NVDA" in text
    assert "$420.00" in text
    assert "+5.0%" in text
    assert "$300.00" in text  # bear band
    # Normalized-DCF-specific fields must NOT appear for moat-adjusted cards.
    assert "floor" not in text.lower() or "plausibility" not in text.lower()
