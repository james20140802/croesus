"""
Orchestration: record live cohorts and evaluate their realized returns.

``record_cohort`` re-scores the live universe with a candidate weight scheme
(without touching the canonical screening_results) and persists the top-N it
would have bought today, with entry prices. ``evaluate_cohorts`` measures every
stored cohort's realized return to an evaluation date against SPY — all
out-of-sample, no look-ahead.
"""
from __future__ import annotations

from datetime import date
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.factors.equity.normalized import QUALITY_OK
from croesus.factors.equity.normalized_repository import NormalizedDcfRepository
from croesus.forward_test.build import build_cohort
from croesus.forward_test.evaluate import evaluate_cohort
from croesus.forward_test.models import CohortPick, CohortReturn
from croesus.forward_test.repository import ForwardTestRepository
from croesus.forward_test.schemes import (
    BENCHMARK_SYMBOL,
    COHORT_TOP_N,
    FORWARD_TEST_SCHEMES,
    NORMALIZED_DCF_SCHEME,
)
from croesus.macro.screening_adapter import neutral_screening_params
from croesus.prices.repository import PriceRepository
from croesus.screening.redundancy import group_keys
from croesus.screening.run_screening import run_screening


def _benchmark_asset_id(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute(
        "SELECT asset_id FROM assets WHERE symbol = ? LIMIT 1", [BENCHMARK_SYMBOL]
    ).fetchone()
    return row[0] if row else None


def _group_of(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT asset_id, name, asset_type FROM assets WHERE is_active"
    ).fetchall()
    return group_keys({aid: (name or "", atype or "") for aid, name, atype in rows})


def record_cohort(
    conn: duckdb.DuckDBPyConnection,
    scheme: str,
    *,
    as_of_date: date | None = None,
    top_n: int = COHORT_TOP_N,
    log: Callable[[str], None] = print,
) -> list[CohortPick]:
    """Score the universe with ``scheme`` and persist the cohort it would buy."""
    if scheme not in FORWARD_TEST_SCHEMES:
        raise ValueError(
            f"unknown scheme {scheme!r}; known: {sorted(FORWARD_TEST_SCHEMES)}"
        )

    params = neutral_screening_params()
    params["factor_weights"] = dict(FORWARD_TEST_SCHEMES[scheme])
    # Rank the whole universe; the cohort top-N cut happens after pricing.
    params["candidate_count"] = len(AssetRepository(conn).list_active())

    result = run_screening(conn, params, as_of_date=as_of_date, persist=False)
    as_of = result.as_of_date
    scored = [
        (c.asset_id, c.score) for c in result.candidates if c.score is not None
    ]

    prices = PriceRepository(conn)
    entry_prices: dict[str, float] = {}
    for asset_id, _ in scored:
        close = prices.get_latest_close(asset_id, as_of)
        if close is not None:
            entry_prices[asset_id] = close

    picks = build_cohort(
        scheme, as_of, scored, _group_of(conn), entry_prices, top_n=top_n
    )
    ForwardTestRepository(conn).save_cohort(picks)
    log(
        f"recorded cohort {scheme} @ {as_of}: {len(picks)} picks "
        f"({', '.join(p.asset_id for p in picks)})"
    )
    return picks


def record_normalized_dcf_cohort(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of_date: date | None = None,
    top_n: int = COHORT_TOP_N,
    log: Callable[[str], None] = print,
) -> list[CohortPick]:
    """Persist the cohort the normalized-DCF methodology would buy today.

    Unlike the weight schemes, this cohort comes from the ``normalized_dcf``
    ranking, not from ``run_screening``: the cheapest names by plausibility gap
    (smaller = cheaper) among the trustworthy ``ok`` tier only — financials are
    already excluded at compute time and ``reference_unreliable`` names are
    dropped here. ``evaluate_cohorts`` then measures it vs SPY like any other
    scheme. Recording the same (scheme, date) again replaces it.
    """
    as_of = as_of_date or date.today()
    snapshots = NormalizedDcfRepository(conn).load_latest(as_of)
    # score = -gap so the smallest (cheapest) gap sorts to the top of build_cohort.
    scored = [
        (s.asset_id, -s.plausibility_gap)
        for s in snapshots
        if s.valuation_quality == QUALITY_OK and s.plausibility_gap is not None
    ]

    prices = PriceRepository(conn)
    entry_prices: dict[str, float] = {}
    for asset_id, _ in scored:
        close = prices.get_latest_close(asset_id, as_of)
        if close is not None:
            entry_prices[asset_id] = close

    picks = build_cohort(
        NORMALIZED_DCF_SCHEME, as_of, scored, _group_of(conn), entry_prices, top_n=top_n
    )
    ForwardTestRepository(conn).save_cohort(picks)
    log(
        f"recorded cohort {NORMALIZED_DCF_SCHEME} @ {as_of}: {len(picks)} picks "
        f"({', '.join(p.asset_id for p in picks)})"
    )
    return picks


def evaluate_cohorts(
    conn: duckdb.DuckDBPyConnection,
    *,
    eval_date: date | None = None,
    scheme: str | None = None,
) -> list[CohortReturn]:
    """Realized return of every stored cohort to ``eval_date`` vs SPY."""
    repo = ForwardTestRepository(conn)
    prices = PriceRepository(conn)
    as_of_eval = eval_date or date.today()
    benchmark_id = _benchmark_asset_id(conn)

    results: list[CohortReturn] = []
    for sch, cohort_date in repo.cohort_dates(scheme):
        picks = repo.load_cohort(sch, cohort_date)
        exit_prices = {
            p.asset_id: close
            for p in picks
            if (close := prices.get_latest_close(p.asset_id, as_of_eval)) is not None
        }
        bench_entry = (
            prices.get_latest_close(benchmark_id, cohort_date)
            if benchmark_id
            else None
        )
        bench_exit = (
            prices.get_latest_close(benchmark_id, as_of_eval)
            if benchmark_id
            else None
        )
        results.append(
            evaluate_cohort(
                picks,
                exit_prices,
                benchmark_entry=bench_entry,
                benchmark_exit=bench_exit,
                eval_date=as_of_eval,
            )
        )
    return results
