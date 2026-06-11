"""Sprint 008b: valuation dimension, persisted sub-scores, expensive guard."""
from datetime import date
from pathlib import Path

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.macro.screening_adapter import neutral_screening_params
from croesus.portfolio.models import PolicyDrift
from croesus.portfolio.rebalancing import generate_proposed_actions
from croesus.profiles.models import AssetType, Currency, InvestorProfile, TradeMode
from croesus.screening.models import ScreeningCandidate
from croesus.screening.repository import ScreeningRepository
from croesus.screening.run_screening import run_screening

AS_OF = date(2026, 6, 1)

_PRICE_FACTORS = {
    "momentum_1m": 0.10,
    "momentum_3m": 0.20,
    "momentum_6m": 0.30,
    "liquidity_1m": 50_000_000.0,
    "above_200d_ma": 1.0,
    "volatility_3m": 0.20,
}


def _seed(conn, valuation_by_asset: dict[str, dict[str, float]]) -> None:
    AssetRepository(conn).upsert_many(
        [
            Asset(f"US_EQ_{symbol}", symbol, f"{symbol} Inc.", "equity",
                  country="US", currency="USD", sector="Technology", source="test")
            for symbol in ("CHEAP", "DEAR", "NOVAL")
        ]
    )
    rows = []
    for asset_id in ("US_EQ_CHEAP", "US_EQ_DEAR", "US_EQ_NOVAL"):
        # Identical price factors so only valuation differentiates the scores.
        for name, value in _PRICE_FACTORS.items():
            rows.append((asset_id, AS_OF, name, value))
        for name, value in valuation_by_asset.get(asset_id, {}).items():
            rows.append((asset_id, AS_OF, name, value))
    conn.executemany(
        "INSERT INTO factor_values (asset_id, date, factor_name, value) VALUES (?, ?, ?, ?)",
        rows,
    )


_VALUATIONS = {
    "US_EQ_CHEAP": {
        "pe_ratio": 10.0, "pb_ratio": 1.5, "ev_to_ebitda": 6.0,
        "fcf_yield": 0.08, "price_to_intrinsic": 0.8,
    },
    "US_EQ_DEAR": {
        "pe_ratio": 45.0, "pb_ratio": 12.0, "ev_to_ebitda": 30.0,
        "fcf_yield": 0.01, "price_to_intrinsic": 1.6,
    },
    # US_EQ_NOVAL: no fundamentals at all.
}


def _run(conn, **param_overrides):
    params = neutral_screening_params() | {"candidate_count": 3} | param_overrides
    return run_screening(conn, params, as_of_date=AS_OF)


def test_cheap_asset_outranks_expensive_on_valuation(tmp_path: Path) -> None:
    db_path = tmp_path / "v.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed(conn, _VALUATIONS)
        result = _run(conn)

    by_id = {c.asset_id: c for c in result.candidates}
    cheap, dear = by_id["US_EQ_CHEAP"], by_id["US_EQ_DEAR"]
    assert cheap.factor_scores["valuation_score"] > dear.factor_scores["valuation_score"]
    # Identical price factors -> the valuation dimension decides the ranking.
    assert cheap.score > dear.score
    assert cheap.rank < dear.rank


def test_missing_fundamentals_renormalizes_instead_of_skipping(tmp_path: Path) -> None:
    db_path = tmp_path / "v.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed(conn, _VALUATIONS)
        result = _run(conn)

    by_id = {c.asset_id: c for c in result.candidates}
    assert "US_EQ_NOVAL" in by_id  # ranked, not skipped
    noval = by_id["US_EQ_NOVAL"]
    assert noval.factor_scores["valuation_score"] is None
    # With identical price factors and the valuation weight renormalized away,
    # the no-fundamentals asset scores between the cheap and expensive names.
    assert by_id["US_EQ_CHEAP"].score > noval.score > by_id["US_EQ_DEAR"].score


def test_zero_valuation_weight_reproduces_price_only_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "v.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed(conn, _VALUATIONS)
        v1_weights = {
            "momentum": 0.35, "liquidity": 0.25, "trend": 0.25,
            "volatility_penalty": 0.15,
        }
        without_key = _run(conn, factor_weights=dict(v1_weights))
        with_zero = _run(conn, factor_weights=v1_weights | {"valuation": 0.0})

    scores_a = {c.asset_id: c.score for c in without_key.candidates}
    scores_b = {c.asset_id: c.score for c in with_zero.candidates}
    assert scores_a == scores_b  # valuation off -> pre-008b composite exactly


def test_factor_scores_persist_horizons_and_raw_multiples(tmp_path: Path) -> None:
    db_path = tmp_path / "v.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed(conn, _VALUATIONS)
        result = _run(conn)
        persisted = ScreeningRepository(conn).list_results(result.run_id)

    cheap = next(c for c in persisted if c.asset_id == "US_EQ_CHEAP")
    fs = cheap.factor_scores
    for key in (
        "momentum_1m_pct", "momentum_3m_pct", "momentum_6m_pct",
        "valuation_score", "pe_ratio", "pb_ratio", "ev_to_ebitda",
        "fcf_yield", "price_to_intrinsic", "above_200d_ma", "trend_gate_active",
    ):
        assert key in fs, f"missing {key} in persisted factor_scores"
    assert fs["pe_ratio"] == 10.0  # raw value, not a percentile


def test_candidate_count_clamps_to_universe_and_exposes_sizes(tmp_path: Path) -> None:
    db_path = tmp_path / "v.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        _seed(conn, _VALUATIONS)
        result = _run(conn, candidate_count=20)

    params = result.screening_params
    assert params["universe_size"] == 3
    assert params["ranked_count"] == 3
    assert params["effective_candidate_count"] == 3  # min(20, ranked)


# ── rebalancing guard ─────────────────────────────────────────────────────────

def _profile() -> InvestorProfile:
    return InvestorProfile(
        profile_id="default", name="Default", base_currency=Currency.USD,
        expected_annual_return=0.08, max_tolerable_drawdown=-0.25,
        investment_horizon_years=10, monthly_contribution=1000.0,
        liquidity_buffer_months=6.0,
        allowed_asset_types=[AssetType.EQUITY, AssetType.ETF, AssetType.CASH],
        disallowed_asset_types=[], max_single_position_weight=0.10,
        max_sector_weight=0.35, max_industry_weight=0.25, max_theme_weight=0.25,
        max_country_weight=0.80, max_currency_weight=0.90,
        max_monthly_turnover=0.30, rebalance_band=0.05,
        trade_mode=TradeMode.PROPOSE_ONLY, metadata={},
    )


def _screen_candidate(price_to_intrinsic: float | None) -> ScreeningCandidate:
    return ScreeningCandidate(
        run_id="screen-1",
        asset_id="US_EQ_MSFT",
        score=0.9,
        rank=1,
        decision_bucket="candidate",
        reason="passes screen",
        reason_codes=[],
        factor_scores={"price_to_intrinsic": price_to_intrinsic},
        metadata={"sleeve_name": "satellite_equity"},
    )


def _actions_for(price_to_intrinsic: float | None):
    drift = PolicyDrift(
        portfolio_id="default", as_of_date=AS_OF, sleeve_name="satellite_equity",
        current_weight=0.10, target_weight=0.15, min_weight=0.0, max_weight=0.20,
        drift=-0.05, is_outside_band=True,
    )
    return generate_proposed_actions(
        "run-1",
        portfolio_id="default",
        as_of_date=AS_OF,
        profile=_profile(),
        total_market_value=100_000.0,
        drifts=[drift],
        screening_candidates=[_screen_candidate(price_to_intrinsic)],
    )


def test_expensive_candidate_goes_to_watch_with_valuation_code() -> None:
    actions = _actions_for(price_to_intrinsic=1.6)
    watch = next(a for a in actions if a.action_type == "watch")
    assert "VALUATION_TOO_EXPENSIVE" in watch.reason_codes
    assert not any(a.action_type == "add" for a in actions)
    assert "60% above DCF intrinsic" in watch.human_readable_reason


def test_fair_or_unvalued_candidate_still_adds() -> None:
    for pti in (0.9, None):
        actions = _actions_for(price_to_intrinsic=pti)
        add = next(a for a in actions if a.action_type == "add")
        assert add.asset_id == "US_EQ_MSFT"
        assert "VALUATION_TOO_EXPENSIVE" not in add.reason_codes
