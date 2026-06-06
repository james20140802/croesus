from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True)
class ScreeningCandidate:
    run_id: str
    asset_id: str
    score: float | None
    rank: int | None
    decision_bucket: str
    reason: str
    reason_codes: list[str] = field(default_factory=list)
    factor_scores: dict[str, float | None] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScreeningRunResult:
    run_id: str
    as_of_date: date
    candidates: list[ScreeningCandidate]
    skipped: list[ScreeningCandidate]
    screening_params: dict[str, Any]


@dataclass(frozen=True)
class SectorThemeScore:
    exposure_type: str
    exposure_name: str
    score: float
    asset_count: int
    current_weight: float | None = None
    limit_weight: float | None = None
    is_overexposed: bool = False
