"""Realized, out-of-sample return of a cohort vs the benchmark.

Every figure here is measured forward from the cohort date using stored prices,
so there is no look-ahead — this is the honest evidence the backtest cannot
provide for valuation-based schemes.
"""
from __future__ import annotations

from datetime import date

from croesus.forward_test.models import CohortPick, CohortReturn


def evaluate_cohort(
    picks: list[CohortPick],
    exit_prices: dict[str, float],
    *,
    benchmark_entry: float | None,
    benchmark_exit: float | None,
    eval_date: date,
) -> CohortReturn:
    """Weighted realized return of ``picks`` to ``eval_date``, vs the benchmark.

    A pick with no exit price (e.g. delisted, no quote yet) is dropped and the
    remaining weights renormalize, so a missing quote never masquerades as a
    -100% position. ``cohort_return`` is ``None`` when nothing is priced.
    """
    scheme = picks[0].cohort_scheme if picks else ""
    as_of = picks[0].as_of_date if picks else eval_date

    priced = [(p, exit_prices[p.asset_id]) for p in picks if p.asset_id in exit_prices]
    total_weight = sum(p.weight for p, _ in priced)

    cohort_return: float | None
    if not priced or total_weight <= 0:
        cohort_return = None
    else:
        cohort_return = sum(
            (p.weight / total_weight) * (exit / p.entry_price - 1.0)
            for p, exit in priced
        )

    benchmark_return: float | None = None
    if benchmark_entry not in (None, 0) and benchmark_exit is not None:
        benchmark_return = benchmark_exit / benchmark_entry - 1.0

    excess: float | None = None
    if cohort_return is not None and benchmark_return is not None:
        excess = cohort_return - benchmark_return

    return CohortReturn(
        cohort_scheme=scheme,
        as_of_date=as_of,
        eval_date=eval_date,
        days_held=(eval_date - as_of).days,
        n_picks=len(picks),
        n_priced=len(priced),
        cohort_return=cohort_return,
        benchmark_return=benchmark_return,
        excess_return=excess,
    )
