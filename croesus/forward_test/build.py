"""Cohort construction: turn a scheme's ranking into a dated, weighted cohort.

Pure with respect to the database: it takes already-scored assets, a redundancy
group map, and entry prices, and returns the cohort the recorder will persist.
Selection mirrors the backtest — top-N by score, redundancy-group-capped equal
weight — so live forward-test cohorts are constructed the same way the backtest
holds positions.
"""
from __future__ import annotations

from datetime import date

from croesus.forward_test.models import CohortPick
from croesus.screening.redundancy import group_equal_weights


def build_cohort(
    scheme: str,
    as_of_date: date,
    scored: list[tuple[str, float]],
    group_of: dict[str, str],
    entry_prices: dict[str, float],
    *,
    top_n: int,
) -> list[CohortPick]:
    """Select the top-N scoreable, priced names and weight them by group.

    ``scored`` is ``(asset_id, score)`` in any order. A name without an entry
    price cannot be bought, so it is skipped before the top-N cut (the cohort is
    what you could actually have entered that day). Weights are
    redundancy-group-capped so two share classes never occupy two full slots.
    """
    ranked = sorted(scored, key=lambda item: (-item[1], item[0]))
    priced = [(aid, score) for aid, score in ranked if aid in entry_prices]
    selected = priced[:top_n]
    if not selected:
        return []

    ids = [aid for aid, _ in selected]
    weights = group_equal_weights(ids, group_of)
    score_by_id = dict(selected)
    return [
        CohortPick(
            cohort_scheme=scheme,
            as_of_date=as_of_date,
            asset_id=aid,
            rank=index,
            score=score_by_id[aid],
            weight=weights[aid],
            entry_price=entry_prices[aid],
        )
        for index, aid in enumerate(ids, start=1)
    ]
