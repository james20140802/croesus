from datetime import date

from croesus.opportunities.risk_gate import (
    DEFAULT_MIN_LIQUIDITY_USD,
    RiskGateVerdict,
    evaluate_risk_gate,
)
from croesus.portfolio.models import AssetAttrs, Exposure
from croesus.profiles.models import AssetType, Currency, InvestorProfile, TradeMode


def _profile(*, allowed=None, disallowed=None) -> InvestorProfile:
    return InvestorProfile(
        profile_id="default", name="Default", base_currency=Currency.USD,
        expected_annual_return=0.08, max_tolerable_drawdown=-0.30,
        investment_horizon_years=10, monthly_contribution=0.0,
        liquidity_buffer_months=6.0,
        allowed_asset_types=allowed or [], disallowed_asset_types=disallowed or [],
        max_single_position_weight=0.10, max_sector_weight=0.25,
        max_industry_weight=0.20, max_theme_weight=0.30,
        max_country_weight=0.80, max_currency_weight=0.80,
        max_monthly_turnover=0.20, rebalance_band=0.05,
        trade_mode=TradeMode.PROPOSE_ONLY, metadata={},
    )


def _exposure(exposure_type, name, weight, cap, is_violation) -> Exposure:
    return Exposure(
        portfolio_id="default", as_of_date=date(2026, 6, 23),
        exposure_type=exposure_type, exposure_name=name, weight=weight,
        market_value=weight * 1000, limit_weight=cap, is_violation=is_violation,
    )


def _attrs(**kw) -> AssetAttrs:
    return AssetAttrs(
        asset_type=kw.get("asset_type", "equity"),
        sector=kw.get("sector", "Healthcare"),
        industry=kw.get("industry", "Pharma"),
        country=kw.get("country", "US"),
        currency=kw.get("currency", "USD"),
    )


def test_clean_candidate_passes():
    v = evaluate_risk_gate(
        "LLY", _attrs(sector="Healthcare"),
        exposures=[_exposure("sector", "Technology", 0.30, 0.25, True)],
        held_asset_ids=set(), profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "pass"
    assert v.reason_codes == []


def test_sector_over_cap_blocks():
    v = evaluate_risk_gate(
        "NVDA", _attrs(sector="Technology"),
        exposures=[_exposure("sector", "Technology", 0.30, 0.25, True)],
        held_asset_ids=set(), profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "SECTOR_OVER_MAX" in v.reason_codes


def test_already_held_position_over_cap_blocks():
    v = evaluate_risk_gate(
        "AAPL", _attrs(sector="Technology"),
        exposures=[_exposure("position", "AAPL", 0.12, 0.10, True)],
        held_asset_ids={"AAPL"}, profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "POSITION_OVER_MAX" in v.reason_codes
    assert any("ALREADY_HELD" in n for n in v.notes)


def test_disallowed_asset_type_blocks():
    v = evaluate_risk_gate(
        "X", _attrs(asset_type="crypto"),
        exposures=[], held_asset_ids=set(),
        profile=_profile(disallowed=[AssetType.CRYPTO]),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "DISALLOWED_ASSET_TYPE" in v.reason_codes


def test_asset_type_not_in_allowlist_blocks():
    v = evaluate_risk_gate(
        "X", _attrs(asset_type="reit"),
        exposures=[], held_asset_ids=set(),
        profile=_profile(allowed=[AssetType.EQUITY, AssetType.ETF]),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "DISALLOWED_ASSET_TYPE" in v.reason_codes


def test_low_liquidity_warns():
    v = evaluate_risk_gate(
        "TINY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=100_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "warn"
    assert "LIQUIDITY_BELOW_MINIMUM" in v.reason_codes


def test_missing_liquidity_warns():
    v = evaluate_risk_gate(
        "TINY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=None, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "warn"
    assert "LIQUIDITY_BELOW_MINIMUM" in v.reason_codes


def test_liquidity_check_disabled_when_floor_zero():
    v = evaluate_risk_gate(
        "TINY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=None, min_liquidity_usd=0,
    )
    assert v.status == "pass"


def test_empty_portfolio_passes_eligibility_only():
    v = evaluate_risk_gate(
        "LLY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "pass"


def test_block_precedence_over_warn():
    v = evaluate_risk_gate(
        "NVDA", _attrs(sector="Technology"),
        exposures=[_exposure("sector", "Technology", 0.30, 0.25, True)],
        held_asset_ids=set(), profile=_profile(),
        liquidity_value=100_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "SECTOR_OVER_MAX" in v.reason_codes
    assert "LIQUIDITY_BELOW_MINIMUM" in v.reason_codes


def test_verdict_is_frozen_dataclass():
    v = RiskGateVerdict("pass", [], [])
    assert v.status == "pass"


# ── orchestrator (evaluate_candidates) ────────────────────────────────────────
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.opportunities.risk_gate import evaluate_candidates
from croesus.profiles.seed_default_profile import seed_default_profile


def _seed_asset(conn, asset_id, **kw):
    conn.execute(
        """INSERT INTO assets
           (asset_id, symbol, name, asset_type, country, exchange, currency,
            sector, industry, is_active, source, metadata)
           VALUES (?, ?, ?, ?, ?, 'NMS', ?, ?, ?, true, 'test', '{}')""",
        [asset_id, kw.get("symbol", asset_id), kw.get("name", asset_id),
         kw.get("asset_type", "equity"), kw.get("country", "US"),
         kw.get("currency", "USD"), kw.get("sector", "Technology"),
         kw.get("industry", "Software")],
    )


def test_evaluate_candidates_missing_profile_returns_empty(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        out = evaluate_candidates(
            conn, ["EQ1"], portfolio_id="default",
            profile_id="nope", as_of_date=date(2026, 6, 23),
        )
    assert out == {}


def test_evaluate_candidates_empty_portfolio_eligibility_only(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        seed_default_profile(conn)
        _seed_asset(conn, "EQ1", sector="Technology")
        conn.execute(
            "INSERT INTO factor_values VALUES ('EQ1', ?, 'liquidity_1m', 5000000)",
            [date(2026, 6, 23)],
        )
        out = evaluate_candidates(
            conn, ["EQ1"], portfolio_id="default",
            profile_id="default", as_of_date=date(2026, 6, 23),
        )
    assert out["EQ1"].status == "pass"


def test_evaluate_candidates_low_liquidity_warns(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        seed_default_profile(conn)
        _seed_asset(conn, "EQ1", sector="Technology")
        conn.execute(
            "INSERT INTO factor_values VALUES ('EQ1', ?, 'liquidity_1m', 50000)",
            [date(2026, 6, 23)],
        )
        out = evaluate_candidates(
            conn, ["EQ1"], portfolio_id="default",
            profile_id="default", as_of_date=date(2026, 6, 23),
        )
    assert out["EQ1"].status == "warn"
    assert "LIQUIDITY_BELOW_MINIMUM" in out["EQ1"].reason_codes
