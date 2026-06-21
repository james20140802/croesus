from __future__ import annotations

from pathlib import Path

from croesus.opportunities.review import OpportunityCard, OpportunityReviewResult
from croesus.reports.paths import report_output_dir
from croesus.reports.registry import register_many

REPORT_TYPE_OPPORTUNITY = "opportunity"


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:+.1f}%"


def _grade_line(card: OpportunityCard) -> str:
    return (
        f"moat={card.moat_grade or 'n/a'}, tech={card.tech_grade or 'n/a'}, "
        f"sector={card.sector_grade or 'n/a'}, "
        f"disruption={card.disruption_grade or 'n/a'}"
    )


def _evidence_line(card: OpportunityCard) -> str:
    return (
        f"moat: {card.moat_evidence or 'n/a'}; "
        f"tech: {card.tech_evidence or 'n/a'}; "
        f"sector: {card.sector_evidence or 'n/a'}; "
        f"disruption: {card.disruption_evidence or 'n/a'}"
    )


def render_opportunity_review(result: OpportunityReviewResult) -> str:
    lines = [
        f"# Opportunity Review - {result.as_of_date:%Y-%m-%d}",
        "",
        f"Methodology: {result.methodology.label}",
        "Boundary: recommendation-only; no trades.",
        "",
    ]
    if not result.cards:
        lines.append("No opportunity cards found for this methodology/date.")
        lines.append("")
        return "\n".join(lines)

    for card in result.cards:
        bands = card.band_intrinsic_by_scenario
        name = f" - {card.name}" if card.name else ""
        lines.extend(
            [
                f"## {card.symbol}{name}",
                "",
                f"- Current price: {_money(card.current_price)}",
                f"- Mechanical base DCF: {_money(card.mechanical_intrinsic_value)} "
                f"({_pct(card.mechanical_upside_pct)})",
                "- Moat-adjusted DCF bear/base/bull: "
                f"{_money(bands.get('bear'))} / {_money(bands.get('base'))} / "
                f"{_money(bands.get('bull'))}",
                f"- Base band upside: {_pct(card.base_upside_pct)}",
                f"- Thesis grades: {_grade_line(card)}",
                f"- Thesis evidence: {_evidence_line(card)}",
                f"- Confidence: {card.thesis_confidence or 'n/a'}; "
                f"evidence_source={card.evidence_source or 'n/a'}",
                f"- Bear case: {card.bear_case or 'n/a'}",
                "",
            ]
        )
    return "\n".join(lines)


def write_opportunity_review_report(
    result: OpportunityReviewResult,
    *,
    reports_dir: str | Path = "reports",
    conn=None,
) -> Path:
    output_dir = report_output_dir(reports_dir, "opportunity", result.as_of_date)
    path = output_dir / "opportunity.md"
    path.write_text(render_opportunity_review(result), encoding="utf-8")
    if conn is not None:
        register_many(conn, REPORT_TYPE_OPPORTUNITY, [path], as_of_date=result.as_of_date)
    return path
