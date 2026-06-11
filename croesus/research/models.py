"""
Research-note model (Sprint 010).

A ``ResearchNote`` is a qualitative annotation a local LLM attaches to one
rebalance proposal: what the business is, what could move it, what could hurt
it — interpreted strictly from the pipeline's own quantitative data. Notes
never carry trade instructions; the schema has no quantity, price, or side.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

STATUS_GENERATED = "generated"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class ResearchNote:
    note_id: str
    run_id: str
    action_id: str | None
    asset_id: str
    as_of_date: date
    model: str
    status: str
    business_summary: str | None = None
    catalysts: str | None = None
    risk_factors: str | None = None
    # Local models have no web access and a fixed training cutoff; this flag
    # travels with the note so reports always carry the warning.
    knowledge_cutoff_caveat: bool = True
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
