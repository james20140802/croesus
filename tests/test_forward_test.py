"""Forward-test harness: cohort construction and realized-return evaluation.

All synthetic and deterministic — no network, no real DB for the pure cores.
"""
from __future__ import annotations

from datetime import date

from croesus.forward_test.build import build_cohort
from croesus.forward_test.evaluate import evaluate_cohort
from croesus.forward_test.models import CohortPick

AS_OF = date(2026, 6, 1)


# ── build_cohort (selection + redundancy-capped weighting) ────────────────────

def test_build_cohort_takes_top_n_and_group_caps_redundant_pair() -> None:
    scored = [
        ("US_EQ_GOOG", 0.95),
        ("US_EQ_GOOGL", 0.92),
        ("US_EQ_AAPL", 0.90),
        ("US_EQ_MSFT", 0.80),
        ("US_EQ_LOW", 0.10),  # out of top 3
    ]
    group_of = {
        "US_EQ_GOOG": "issuer:alphabet",
        "US_EQ_GOOGL": "issuer:alphabet",
        "US_EQ_AAPL": "US_EQ_AAPL",
        "US_EQ_MSFT": "US_EQ_MSFT",
        "US_EQ_LOW": "US_EQ_LOW",
    }
    prices = {
        "US_EQ_GOOG": 150.0, "US_EQ_GOOGL": 148.0,
        "US_EQ_AAPL": 200.0, "US_EQ_MSFT": 400.0, "US_EQ_LOW": 10.0,
    }
    picks = build_cohort(
        "composite_v2_value", AS_OF, scored, group_of, prices, top_n=3
    )

    by_id = {p.asset_id: p for p in picks}
    assert set(by_id) == {"US_EQ_GOOG", "US_EQ_GOOGL", "US_EQ_AAPL"}
    # Alphabet group capped at one of the three slots, split between its classes.
    assert by_id["US_EQ_GOOG"].weight == by_id["US_EQ_GOOGL"].weight
    assert (
        by_id["US_EQ_GOOG"].weight + by_id["US_EQ_GOOGL"].weight
    ) == by_id["US_EQ_AAPL"].weight
    assert abs(sum(p.weight for p in picks) - 1.0) < 1e-9
    assert by_id["US_EQ_GOOG"].rank == 1
    assert by_id["US_EQ_GOOG"].entry_price == 150.0


def test_build_cohort_skips_names_without_an_entry_price() -> None:
    scored = [("A", 0.9), ("B", 0.8), ("C", 0.7)]
    group_of = {"A": "A", "B": "B", "C": "C"}
    prices = {"A": 100.0, "C": 50.0}  # B has no price → cannot be entered
    picks = build_cohort("composite_live", AS_OF, scored, group_of, prices, top_n=3)
    assert {p.asset_id for p in picks} == {"A", "C"}
    assert abs(sum(p.weight for p in picks) - 1.0) < 1e-9


# ── evaluate_cohort (realized, out-of-sample return vs benchmark) ─────────────

def _pick(asset_id: str, weight: float, entry: float) -> CohortPick:
    return CohortPick("s", AS_OF, asset_id, 1, 0.0, weight, entry)


def test_evaluate_cohort_weighted_return_and_excess() -> None:
    picks = [
        _pick("A", 0.5, 100.0),  # +20%
        _pick("B", 0.5, 200.0),  # -10%
    ]
    exit_prices = {"A": 120.0, "B": 180.0}
    result = evaluate_cohort(
        picks, exit_prices,
        benchmark_entry=400.0, benchmark_exit=440.0,  # +10%
        eval_date=date(2026, 6, 30),
    )
    assert result.n_priced == 2
    assert abs(result.cohort_return - 0.05) < 1e-9  # 0.5*0.20 + 0.5*(-0.10)
    assert abs(result.benchmark_return - 0.10) < 1e-9
    assert abs(result.excess_return - (-0.05)) < 1e-9
    assert result.days_held == 29


def test_evaluate_cohort_renormalizes_over_priced_names() -> None:
    # B has no exit price (e.g. delisted) — its weight must not silently count
    # as a -100%; the realized return renormalizes over the priced names.
    picks = [_pick("A", 0.5, 100.0), _pick("B", 0.5, 200.0)]
    exit_prices = {"A": 110.0}  # only A priced → +10%
    result = evaluate_cohort(
        picks, exit_prices, benchmark_entry=100.0, benchmark_exit=100.0,
        eval_date=date(2026, 6, 11),
    )
    assert result.n_priced == 1
    assert abs(result.cohort_return - 0.10) < 1e-9  # A alone, reweighted to 1.0
    assert result.benchmark_return == 0.0


def test_evaluate_cohort_none_when_nothing_priced() -> None:
    picks = [_pick("A", 1.0, 100.0)]
    result = evaluate_cohort(
        picks, {}, benchmark_entry=None, benchmark_exit=None,
        eval_date=date(2026, 6, 11),
    )
    assert result.n_priced == 0
    assert result.cohort_return is None
    assert result.excess_return is None


# ── repository round-trip (real temp DB) ──────────────────────────────────────

def test_repository_round_trips_and_upserts(tmp_path) -> None:
    from croesus.db.connection import get_connection
    from croesus.db.migrate import migrate
    from croesus.forward_test.repository import ForwardTestRepository

    db = tmp_path / "ft.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        repo = ForwardTestRepository(conn)
        repo.save_cohort([
            CohortPick("composite_v2_value", AS_OF, "A", 1, 0.9, 0.6, 100.0),
            CohortPick("composite_v2_value", AS_OF, "B", 2, 0.8, 0.4, 50.0),
        ])
        loaded = repo.load_cohort("composite_v2_value", AS_OF)
        assert [p.asset_id for p in loaded] == ["A", "B"]
        assert loaded[0].weight == 0.6

        # Re-recording the same cohort replaces, never duplicates.
        repo.save_cohort([CohortPick("composite_v2_value", AS_OF, "A", 1, 0.95, 1.0, 110.0)])
        again = repo.load_cohort("composite_v2_value", AS_OF)
        a = next(p for p in again if p.asset_id == "A")
        assert a.entry_price == 110.0
        assert repo.cohort_dates() == [("composite_v2_value", AS_OF)]


# ── integration: evaluate_cohorts pulls real exit prices and beats/lags SPY ────

def test_evaluate_cohorts_measures_realized_return_vs_spy(tmp_path) -> None:
    from croesus.db.connection import get_connection
    from croesus.db.migrate import migrate
    from croesus.forward_test.repository import ForwardTestRepository
    from croesus.forward_test.run import evaluate_cohorts

    db = tmp_path / "ft.duckdb"
    migrate(db)
    d0, d1 = date(2026, 1, 2), date(2026, 3, 2)
    with get_connection(db) as conn:
        conn.execute(
            "INSERT INTO assets (asset_id, symbol, name, asset_type, source) VALUES "
            "('US_EQ_WIN','WIN','Winner Inc','equity','test'),"
            "('US_ETF_SPY','SPY','SPDR S&P 500 ETF Trust','etf','test')"
        )
        for aid, p0, p1 in [("US_EQ_WIN", 100.0, 130.0), ("US_ETF_SPY", 400.0, 440.0)]:
            conn.execute(
                "INSERT INTO prices_daily (asset_id, date, close, source) VALUES (?,?,?,?),(?,?,?,?)",
                [aid, d0, p0, "test", aid, d1, p1, "test"],
            )
        ForwardTestRepository(conn).save_cohort(
            [CohortPick("composite_v2_value", d0, "US_EQ_WIN", 1, 0.9, 1.0, 100.0)]
        )
        [result] = evaluate_cohorts(conn, eval_date=d1)

    assert result.n_priced == 1
    assert abs(result.cohort_return - 0.30) < 1e-9       # 100 -> 130
    assert abs(result.benchmark_return - 0.10) < 1e-9    # SPY 400 -> 440
    assert abs(result.excess_return - 0.20) < 1e-9       # beat SPY by 20pp
    assert result.days_held == 59


# ── normalized-DCF cohort recorder (DB integration) ──────────────────────────

def test_record_normalized_dcf_cohort_uses_ok_tier_by_gap(tmp_path) -> None:
    import pandas as pd

    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.db.connection import get_connection
    from croesus.db.migrate import migrate
    from croesus.factors.equity.normalized_repository import (
        NormalizedDcfRepository,
        NormalizedDcfSnapshot,
    )
    from croesus.forward_test.repository import ForwardTestRepository
    from croesus.forward_test.run import record_normalized_dcf_cohort
    from croesus.prices.repository import PriceRepository

    d = date(2026, 6, 30)
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        seed_us_equities(conn)
        repo = NormalizedDcfRepository(conn)
        prices = PriceRepository(conn)
        # AAPL ok cheap (gap -0.30), MSFT ok pricier (gap +0.10),
        # NVDA reference_unreliable but "cheaper" (gap -0.90) -> must be excluded.
        for aid, gap, qual in [
            ("US_EQ_AAPL", -0.30, "ok"),
            ("US_EQ_MSFT", 0.10, "ok"),
            ("US_EQ_NVDA", -0.90, "reference_unreliable"),
        ]:
            repo.upsert(NormalizedDcfSnapshot(
                asset_id=aid, date=d, current_price=100.0,
                normalized_base_fcf=50.0, reference_growth=0.05,
                normalized_intrinsic_value_per_share=110.0, normalized_upside_pct=0.1,
                implied_growth=0.05 + gap, plausibility_gap=gap,
                valuation_quality=qual, n_fcf_years=8, wacc=0.10, assumptions={}))
            prices.upsert_daily_prices(aid, pd.DataFrame([{
                "date": d, "open": 100.0, "high": 100.0, "low": 100.0,
                "close": 100.0, "adjusted_close": 100.0, "volume": 1_000_000,
            }]), source="test")

        picks = record_normalized_dcf_cohort(conn, as_of_date=d, log=lambda _m: None)
        ids = [p.asset_id for p in picks]
        assert ids == ["US_EQ_AAPL", "US_EQ_MSFT"]  # ok-tier, cheapest gap first
        assert "US_EQ_NVDA" not in ids  # reference_unreliable excluded from the cohort

        stored = ForwardTestRepository(conn).load_cohort("normalized_dcf", d)
        assert {p.asset_id for p in stored} == {"US_EQ_AAPL", "US_EQ_MSFT"}
