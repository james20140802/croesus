from __future__ import annotations

from croesus.assets.models import Asset
from croesus.research.thesis_evidence import ThesisEvidence

_SYSTEM_PROMPT = """You are an equity analyst grading the structural thesis of a \
company from its SEC filing, recent news, and numbers. Grade FOUR dimensions, \
each on a fixed scale — use ONLY these values:

- moat (durable competitive advantage): wide | narrow | none
- tech (technology capability vs peers): leading | parity | lagging
- sector (sector trajectory): secular_growth | stable | declining
- disruption (risk of being disrupted): low | medium | high

Rules:
- Base every grade on the evidence provided. For each dimension give a one- to \
two-sentence `*_evidence` that cites the filing or news where possible.
- Set `evidence_source` to "filing" only if the grades are defensible from the \
filing text; otherwise "general_knowledge".
- Always give a `bear_case`: the single most credible way this thesis is wrong.
- Give an overall `confidence`: high | medium | low.

Respond with ONE JSON object and nothing else, exactly these keys:
{
  "moat_grade": "...", "moat_evidence": "...",
  "tech_grade": "...", "tech_evidence": "...",
  "sector_grade": "...", "sector_evidence": "...",
  "disruption_grade": "...", "disruption_evidence": "...",
  "bear_case": "...", "confidence": "...", "evidence_source": "..."
}"""


def build_thesis_messages(asset: Asset, evidence: ThesisEvidence) -> list[dict[str, str]]:
    """Render the grading rubric (system) and the assembled evidence (user)."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _render_evidence(asset, evidence)},
    ]


def _render_evidence(asset: Asset, ev: ThesisEvidence) -> str:
    lines: list[str] = [
        f"Company: {asset.name or asset.symbol} ({asset.symbol})",
        f"Sector: {asset.sector or 'n/a'} | Industry: {asset.industry or 'n/a'}",
        "",
    ]

    if ev.valuation is not None:
        v = ev.valuation
        lines += [
            "Valuation snapshot:",
            f"  intrinsic_value_per_share={v.intrinsic_value_per_share} "
            f"current_price={v.current_price} upside_pct={v.upside_pct}",
            "",
        ]

    nums = ", ".join(f"{k}={v}" for k, v in ev.fundamentals.items())
    lines += [f"Key fundamentals: {nums}", ""]

    if ev.news:
        lines.append("Recent news:")
        for n in ev.news:
            headline = n.headline or "(no headline)"
            lines.append(f"  - {headline} [{n.source_name or n.source}]")
        lines.append("")

    if ev.filing_excerpt:
        lines += [
            f"Latest filing ({ev.filing_form}, filed {ev.filing_date}) — excerpt:",
            ev.filing_excerpt,
        ]
    else:
        lines.append("No filing text available.")

    return "\n".join(lines)
