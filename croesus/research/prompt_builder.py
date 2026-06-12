"""
Deterministic prompt construction for research notes (Sprint 010).

A local model has no web access and a fixed training cutoff, so the prompt is
designed around what it *can* do well: interpret the quantitative evidence the
pipeline already computed (factor sub-scores, raw multiples, DCF assumptions,
macro regime) and articulate what they imply and what they cannot show.
"Catalysts" are framed as items for the human to verify against current
sources, never as claims about recent news.

The same inputs always produce the same messages (sections and keys are
sorted), so prompts are diffable and cacheable.
"""
from __future__ import annotations

from typing import Any

SYSTEM_PROMPT = """\
You are a buy-side equity research analyst inside a personal quantitative \
research pipeline. You are running locally with NO web access and a fixed \
training cutoff: do not claim knowledge of recent news, prices, or events. \
Base every statement on the quantitative data provided in the user message, \
plus general durable knowledge about the company's business model.

Hard rules:
- Never recommend, propose, or size a trade. Buy/sell decisions belong to the \
human. Do not use words like "buy", "sell", "add", or "trim" as advice.
- Frame anything time-sensitive as a question for the human to verify against \
current sources.
- If the data is insufficient for a claim, say so instead of guessing.

Respond with a single JSON object and nothing else:
{"business_summary": "...", "catalysts": "...", "risk_factors": "..."}
- business_summary: 2-4 sentences — what the company does and what the \
provided factor/valuation data says about it.
- catalysts: 2-4 sentences — what could support the quantitative picture, \
phrased as items to verify (earnings trajectory, sector dynamics, the DCF \
assumptions holding).
- risk_factors: 2-4 sentences — what could invalidate it (valuation premium, \
momentum reversal, macro regime, model-assumption risk)."""


def build_research_messages(
    *,
    asset: dict[str, Any] | None,
    action: Any,
    candidate: Any | None,
    valuation: Any | None,
    macro_state: Any | None,
) -> list[dict[str, str]]:
    """Build the chat messages for one proposal's research note.

    ``asset`` is a plain dict (asset_id/name/sector/industry); ``action`` is a
    ProposedAction; ``candidate`` a ScreeningCandidate; ``valuation`` a
    ValuationSnapshot; ``macro_state`` a MacroState — all duck-typed so this
    module needs no imports from those packages.
    """
    sections: list[str] = []

    asset = asset or {}
    sections.append(
        _section(
            "Asset",
            {
                "asset_id": asset.get("asset_id") or getattr(action, "asset_id", None),
                "name": asset.get("name"),
                "sector": asset.get("sector"),
                "industry": asset.get("industry"),
            },
        )
    )

    sections.append(
        _section(
            "Proposal under review",
            {
                "action_type": getattr(action, "action_type", None),
                "reason_codes": ", ".join(getattr(action, "reason_codes", []) or []),
                "reason": getattr(action, "human_readable_reason", None),
            },
        )
    )

    if candidate is not None:
        scores = {
            name: _fmt(value)
            for name, value in sorted((candidate.factor_scores or {}).items())
            if value is not None
        }
        sections.append(
            _section(
                "Screening evidence (percentiles in [0,1]; multiples are raw)",
                {"composite_score": _fmt(candidate.score), "rank": candidate.rank}
                | scores,
            )
        )

    if valuation is not None:
        sections.append(
            _section(
                "DCF valuation snapshot",
                {
                    "intrinsic_value_per_share": _fmt(
                        valuation.intrinsic_value_per_share
                    ),
                    "current_price": _fmt(valuation.current_price),
                    "upside_pct": _fmt(valuation.upside_pct),
                    "wacc": _fmt(valuation.wacc),
                    "fcf_growth_rate": _fmt(valuation.fcf_growth_rate),
                    "terminal_growth_rate": _fmt(valuation.terminal_growth_rate),
                },
            )
        )

    if macro_state is not None:
        sections.append(
            _section(
                "Macro context",
                {
                    "regime": macro_state.regime,
                    "regime_confidence": _fmt(macro_state.regime_confidence),
                    "positioning": macro_state.positioning,
                },
            )
        )

    user = (
        "Write the research note for this proposal using only the data below.\n\n"
        + "\n\n".join(sections)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _section(title: str, fields: dict[str, Any]) -> str:
    lines = [f"### {title}"]
    for key, value in fields.items():
        if value is None or value == "":
            continue
        lines.append(f"- {key}: {value}")
    if len(lines) == 1:
        lines.append("- (no data available)")
    return "\n".join(lines)


def _fmt(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)
