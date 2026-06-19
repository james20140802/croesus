from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

STATUS_GENERATED = "generated"
STATUS_FAILED = "failed"

# Discrete grade vocabularies (spec §방법론 A). The first three map to C3's
# DcfKnobs (moat→CAP years, sector→terminal growth, disruption→WACC premium);
# tech is human-review evidence with no knob.
MOAT_GRADES = ("wide", "narrow", "none")
TECH_GRADES = ("leading", "parity", "lagging")
SECTOR_GRADES = ("secular_growth", "stable", "declining")
DISRUPTION_GRADES = ("low", "medium", "high")
CONFIDENCE_LEVELS = ("high", "medium", "low")
# Whether the thesis is defensible from the filing or rests on general knowledge.
EVIDENCE_SOURCES = ("filing", "general_knowledge")


@dataclass(frozen=True)
class ThesisGrade:
    """One asset's structural-thesis grade on a given date.

    A ``failed`` grade carries ``error`` and leaves the grade fields None; a
    ``generated`` grade carries all four dimension grades, their evidence, a
    bear case, a confidence, and an evidence source.
    """

    asset_id: str
    as_of_date: date
    run_id: str
    model: str
    status: str
    moat_grade: str | None = None
    moat_evidence: str | None = None
    tech_grade: str | None = None
    tech_evidence: str | None = None
    sector_grade: str | None = None
    sector_evidence: str | None = None
    disruption_grade: str | None = None
    disruption_evidence: str | None = None
    bear_case: str | None = None
    confidence: str | None = None
    evidence_source: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Not frozen: int counters are reassigned in the grader loop (mirrors
# NewsIngestionResult / ResearchRunResult).
@dataclass
class ThesisRunResult:
    run_id: str
    grades: list[ThesisGrade] = field(default_factory=list)
    generated: int = 0
    failed: int = 0
    skipped_reason: str | None = None
