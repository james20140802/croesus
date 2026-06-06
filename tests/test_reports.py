from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.screening_run import main as screening_main
from croesus.macro.models import MacroState
from croesus.macro.report import save_report as save_macro_report
from croesus.screening.models import ScreeningCandidate, ScreeningRunResult
from croesus.screening.report import save_report as save_screening_report
from croesus.screening.repository import ScreeningRepository

AS_OF = date(2026, 6, 5)


def test_macro_report_uses_type_and_date_directories(tmp_path: Path) -> None:
    state = _macro_state()

    md_path, csv_path = save_macro_report(state, reports_dir=tmp_path)

    assert md_path == tmp_path / "macro" / "2026-06-06" / "macro.md"
    assert csv_path == tmp_path / "macro" / "2026-06-06" / "macro_scores.csv"
    assert "Current Regime: 🟢 Goldilocks" in md_path.read_text(encoding="utf-8")


def test_screening_report_writes_markdown_and_csv_with_explanations(tmp_path: Path) -> None:
    db_path = tmp_path / "report.duckdb"
    migrate(db_path)

    result = ScreeningRunResult(
        run_id="screening-2026-06-05-test",
        as_of_date=AS_OF,
        candidates=[
            ScreeningCandidate(
                run_id="screening-2026-06-05-test",
                asset_id="US_EQ_AAPL",
                score=0.6727,
                rank=1,
                decision_bucket="candidate",
                reason="ranked by macro-adjusted factor score",
                factor_scores={
                    "momentum_score": 0.7273,
                    "liquidity_score": 0.8182,
                    "trend_score": 0.6364,
                    "volatility_penalty": 0.1818,
                },
                metadata={"portfolio_fit": "addable", "blocking_exposures": []},
            ),
            ScreeningCandidate(
                run_id="screening-2026-06-05-test",
                asset_id="US_ETF_VOO",
                score=None,
                rank=None,
                decision_bucket="skipped",
                reason="skipped: missing momentum factors",
                reason_codes=["MISSING_MOMENTUM_FACTORS"],
                factor_scores={
                    "momentum_score": None,
                    "liquidity_score": None,
                    "trend_score": None,
                    "volatility_penalty": None,
                },
                metadata={"portfolio_fit": "watch"},
            ),
        ],
        skipped=[],
        screening_params={
            "regime": "Goldilocks",
            "positioning": "Moderately Aggressive",
            "candidate_count": 20,
            "factor_weights": {
                "momentum": 0.45,
                "liquidity": 0.25,
                "trend": 0.25,
                "volatility_penalty": 0.10,
            },
            "filters": {},
        },
    )

    with get_connection(db_path) as conn:
        AssetRepository(conn).upsert_many(
            [
                Asset(
                    "US_EQ_AAPL",
                    "AAPL",
                    "Apple Inc.",
                    "equity",
                    country="US",
                    sector="Technology",
                    industry="Consumer Electronics",
                    metadata={"theme_tags": ["ai"]},
                ),
                Asset(
                    "US_ETF_VOO",
                    "VOO",
                    "Vanguard S&P 500 ETF",
                    "etf",
                    country="US",
                    sector="Broad Market",
                    industry="Large Blend ETF",
                    metadata={"theme_tags": ["broad_market"]},
                ),
            ]
        )
        ScreeningRepository(conn).upsert_results(result.candidates)
        md_path, csv_path = save_screening_report(conn, result, reports_dir=tmp_path)

    assert md_path == tmp_path / "screening" / "2026-06-05" / "screening-2026-06-05-test.md"
    assert csv_path == tmp_path / "screening" / "2026-06-05" / "screening-2026-06-05-test.csv"

    md = md_path.read_text(encoding="utf-8")
    assert "## Why The Top Candidates Ranked Here" in md
    assert "AAPL ranked #1 because" in md
    assert "momentum contributed" in md
    assert "volatility penalty subtracted" in md
    assert "Technology" in md
    assert "ai" in md
    assert "VOO" in md
    assert "missing momentum factors" in md

    csv = csv_path.read_text(encoding="utf-8")
    assert "symbol,asset_id,rank,score,decision_bucket" in csv
    assert "AAPL,US_EQ_AAPL,1,0.6727,candidate" in csv


def test_screening_run_cli_save_report_option_writes_reports(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "cli.duckdb"
    reports_dir = tmp_path / "reports"
    migrate(db_path)

    with get_connection(db_path) as conn:
        AssetRepository(conn).upsert_many(
            [
                Asset("US_EQ_AAPL", "AAPL", "Apple Inc.", "equity", country="US", sector="Technology"),
                Asset("US_EQ_MSFT", "MSFT", "Microsoft Corp.", "equity", country="US", sector="Technology"),
            ]
        )
        _seed_factor_values(conn)

    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))
    monkeypatch.setattr(
        "sys.argv",
        [
            "python -m croesus.jobs.screening_run",
            "--save-report",
            "--reports-dir",
            str(reports_dir),
        ],
    )

    screening_main()

    generated = sorted((reports_dir / "screening" / "2026-06-05").glob("*.md"))
    assert len(generated) == 1
    assert "Screening Report" in generated[0].read_text(encoding="utf-8")


def _macro_state() -> MacroState:
    return MacroState(
        date=date(2026, 6, 6),
        regime="Goldilocks",
        regime_confidence=0.75,
        growth_direction="Expanding",
        inflation_direction="Falling",
        amplifier_score=16.86,
        confirmation_score=0.0351,
        positioning="Moderately Aggressive",
        raw_indicators={"amp_liquidity": 2.8, "amp_credit": 6.99, "amp_rates": 52.35},
        warnings=[],
        opportunities=[],
        regime_methods={},
    )


def _seed_factor_values(conn) -> None:
    rows = []
    factors = {
        "US_EQ_AAPL": {
            "momentum_1m": 0.10,
            "momentum_3m": 0.20,
            "momentum_6m": 0.30,
            "liquidity_1m": 1000.0,
            "above_200d_ma": 1.0,
            "volatility_3m": 0.10,
        },
        "US_EQ_MSFT": {
            "momentum_1m": 0.01,
            "momentum_3m": 0.02,
            "momentum_6m": 0.03,
            "liquidity_1m": 2000.0,
            "above_200d_ma": 0.0,
            "volatility_3m": 0.20,
        },
    }
    for asset_id, values in factors.items():
        for factor_name, value in values.items():
            rows.append((asset_id, AS_OF, factor_name, value))
    conn.executemany(
        """
        INSERT INTO factor_values (asset_id, date, factor_name, value)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
