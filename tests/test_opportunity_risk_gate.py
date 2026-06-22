from datetime import date

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.opportunities.review import run_opportunity_review
from croesus.profiles.seed_default_profile import seed_default_profile


def _seed_band_asset(conn, asset_id, sector, base_iv, price, liquidity):
    conn.execute(
        """INSERT INTO assets
           (asset_id, symbol, name, asset_type, country, exchange, currency,
            sector, industry, is_active, source, metadata)
           VALUES (?, ?, ?, 'equity', 'US', 'NMS', 'USD', ?, 'Sub', true, 'test', '{}')""",
        [asset_id, asset_id, asset_id, sector],
    )
    d = date(2026, 6, 23)
    for scenario, iv in (
        ("bear", base_iv * 0.7),
        ("base", base_iv),
        ("bull", base_iv * 1.3),
    ):
        conn.execute(
            """INSERT INTO intrinsic_value_bands
               (asset_id, date, scenario, intrinsic_value_per_share, current_price,
                upside_pct, wacc, fcf_growth_rate, terminal_growth_rate,
                explicit_years, wacc_risk_premium, moat_grade, sector_grade,
                disruption_grade, thesis_as_of_date, thesis_run_id)
               VALUES (?, ?, ?, ?, ?, ?, 0.09, 0.05, 0.025, 7, 0.0,
                       'narrow', 'stable', 'medium', ?, 'run-1')""",
            [asset_id, d, scenario, iv, price, (iv - price) / price, d],
        )
    conn.execute(
        "INSERT INTO factor_values VALUES (?, ?, 'liquidity_1m', ?)",
        [asset_id, d, liquidity],
    )


def test_review_attaches_gate_verdicts(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        seed_default_profile(conn)
        _seed_band_asset(conn, "LLY", "Healthcare", 500.0, 400.0, 5_000_000)
        result = run_opportunity_review(
            conn, methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=date(2026, 6, 23),
        )
    card = next(c for c in result.cards if c.asset_id == "LLY")
    assert card.risk_gate is not None
    assert card.risk_gate.status in {"pass", "warn", "block"}
    assert result.gate_summary is not None
    assert sum(result.gate_summary.values()) == len(result.cards)


def test_review_low_liquidity_warns(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        seed_default_profile(conn)
        _seed_band_asset(conn, "TINY", "Healthcare", 500.0, 400.0, 50_000)
        result = run_opportunity_review(
            conn, methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=date(2026, 6, 23),
        )
    card = next(c for c in result.cards if c.asset_id == "TINY")
    assert card.risk_gate.status == "warn"
    assert "LIQUIDITY_BELOW_MINIMUM" in card.risk_gate.reason_codes


def test_review_skips_gate_when_disabled(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        seed_default_profile(conn)
        _seed_band_asset(conn, "LLY", "Healthcare", 500.0, 400.0, 5_000_000)
        result = run_opportunity_review(
            conn, methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=date(2026, 6, 23), apply_risk_gate=False,
        )
    assert all(c.risk_gate is None for c in result.cards)
    assert result.gate_summary is None
