"""Dataclasses for forward-test cohorts and their realized returns."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CohortPick:
    """One name a scheme selected on ``as_of_date``, with its entry price."""

    cohort_scheme: str
    as_of_date: date
    asset_id: str
    rank: int
    score: float
    weight: float
    entry_price: float


@dataclass(frozen=True)
class CohortReturn:
    """Realized, out-of-sample performance of one cohort to an evaluation date."""

    cohort_scheme: str
    as_of_date: date
    eval_date: date
    days_held: int
    n_picks: int
    n_priced: int  # picks with an exit price available
    cohort_return: float | None  # weighted realized return over priced picks
    benchmark_return: float | None
    excess_return: float | None  # cohort - benchmark, when both are available
