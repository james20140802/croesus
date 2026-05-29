from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from croesus.macro.models import MacroState
from croesus.macro.screening_adapter import get_screening_params

_REGIME_EMOJI = {
    "Goldilocks": "🟢",
    "Reflation": "🔴",
    "Stagflation": "🔴",
    "Deflation": "🟡",
}


def _regime_emoji(regime: str) -> str:
    return _REGIME_EMOJI.get(regime, "⚪")


def generate_markdown(state: MacroState, params: dict | None = None) -> str:
    if params is None:
        params = get_screening_params(state)

    emoji = _regime_emoji(state.regime)
    lines: list[str] = [
        f"# Macro Research Report — {state.date}",
        "",
        f"## Current Regime: {emoji} {state.regime}",
        f"> {state.growth_direction} Growth + {state.inflation_direction} Inflation",
        f"> Confidence: {state.regime_confidence * 100:.1f}% | Positioning: **{state.positioning}**",
        "",
        "## Layer 1: Regime",
        "",
        "| Signal | Direction |",
        "|--------|-----------|",
        f"| Growth | {state.growth_direction} |",
        f"| Inflation | {state.inflation_direction} |",
        f"| Regime | {state.regime} |",
        f"| Confidence | {state.regime_confidence:.2f} |",
        "",
        f"## Layer 2: Risk Amplifier — Score {state.amplifier_score:.1f}/100",
        "",
        "| Category | Risk Score (0–100) |",
        "|----------|--------------------|",
        f"| Liquidity | {state.raw_indicators.get('amp_liquidity', 'N/A')} |",
        f"| Credit | {state.raw_indicators.get('amp_credit', 'N/A')} |",
        f"| Rates | {state.raw_indicators.get('amp_rates', 'N/A')} |",
        f"| **Overall Amplifier** | **{state.amplifier_score:.2f}** |",
        "",
        f"## Layer 3: Confirmation — Score {state.confirmation_score:+.2f}",
        "",
        "| Indicator | Last Value |",
        "|-----------|------------|",
    ]

    for key in ("^VIX", "^VIX3M", "^GSPC", "DX-Y.NYB", "HG=F", "GC=F", "CL=F", "aaii_bull_bear", "naaim_exposure"):
        val = state.raw_indicators.get(key)
        if val is not None:
            lines.append(f"| {key} | {val:.4f} |")

    lines += [
        f"| **Confirmation Score** | **{state.confirmation_score:+.4f}** |",
        "",
    ]

    if state.warnings:
        lines += ["## Warnings", ""]
        lines += ["| Indicator | Current | Percentile | Code |", "|-----------|---------|------------|------|"]
        for w in state.warnings:
            lines.append(
                f"| {w['indicator']} | {w['current']:.2f} | {w['percentile']:.0f}th | `{w['code']}` |"
            )
        lines.append("")
    else:
        lines += ["## Warnings", "", "_No active warnings._", ""]

    if state.opportunities:
        lines += ["## Opportunities", ""]
        lines += ["| Indicator | Current | Percentile | Code |", "|-----------|---------|------------|------|"]
        for o in state.opportunities:
            lines.append(
                f"| {o['indicator']} | {o['current']:.2f} | {o['percentile']:.0f}th | `{o['code']}` |"
            )
        lines.append("")
    else:
        lines += ["## Opportunities", "", "_No active opportunities._", ""]

    fw = params.get("factor_weights", {})
    filters = params.get("filters", {})
    candidate_count = params.get("candidate_count", "-")
    lines += [
        "## Screening Adjustments Applied",
        "",
        "**Factor Weights:**",
        "",
    ]
    for k, v in fw.items():
        lines.append(f"- `{k}`: {v:.4f}")
    lines.append("")
    if filters:
        lines.append("**Active Stress Filters:**")
        lines.append("")
        for k, v in filters.items():
            lines.append(f"- `{k}`: ×{v}")
        lines.append("")
    lines.append(f"**Candidate Pool Size:** {candidate_count}")
    lines.append("")

    return "\n".join(lines)


def save_report(
    state: MacroState,
    reports_dir: str | Path = "reports",
    raw_indicators: dict | None = None,
) -> tuple[Path, Path]:
    """
    Write Markdown and CSV reports for `state`.

    Returns (md_path, csv_path).
    """
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    params = get_screening_params(state)
    md_content = generate_markdown(state, params)

    date_str = str(state.date)
    md_path = reports_dir / f"macro_{date_str}.md"
    md_path.write_text(md_content, encoding="utf-8")

    # CSV — append to cumulative file if present, else create
    csv_path = reports_dir / f"macro_scores_{date_str}.csv"
    row = {
        "date": str(state.date),
        "regime": state.regime,
        "regime_confidence": state.regime_confidence,
        "amplifier_score": state.amplifier_score,
        "confirmation_score": state.confirmation_score,
        "positioning": state.positioning,
        "growth_direction": state.growth_direction,
        "inflation_direction": state.inflation_direction,
    }
    if raw_indicators:
        row.update({k: v for k, v in raw_indicators.items() if isinstance(v, (int, float))})

    fieldnames = list(row.keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(row)

    return md_path, csv_path
