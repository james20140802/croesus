from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.screening.models import ScreeningCandidate
from croesus.screening.repository import ScreeningRepository
from croesus.screening.sector_theme import compute_sector_theme_scores

AS_OF = date(2026, 6, 1)


def test_sector_scores_aggregate_candidate_scores(tmp_path: Path) -> None:
    db_path = tmp_path / "sector.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_candidates(conn)

        scores = compute_sector_theme_scores(conn, "run-1", as_of_date=AS_OF)

    sector_scores = {
        score.exposure_name: score
        for score in scores
        if score.exposure_type == "sector"
    }
    assert round(sector_scores["Technology"].score, 4) == 0.75
    assert sector_scores["Technology"].asset_count == 2
    assert sector_scores["Communication Services"].score == 0.4


def test_theme_scores_read_asset_metadata_theme_tags(tmp_path: Path) -> None:
    db_path = tmp_path / "theme.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_candidates(conn)

        scores = compute_sector_theme_scores(conn, "run-1", as_of_date=AS_OF)

    theme_scores = {
        score.exposure_name: score
        for score in scores
        if score.exposure_type == "theme"
    }
    assert round(theme_scores["ai"].score, 4) == 0.75
    assert theme_scores["ai"].asset_count == 2
    assert theme_scores["social"].score == 0.4


def test_overexposed_sector_is_flagged_from_portfolio_exposures(tmp_path: Path) -> None:
    db_path = tmp_path / "overlay.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        _seed_candidates(conn)
        conn.execute(
            """
            INSERT INTO portfolio_exposures (
              portfolio_id, as_of_date, exposure_type, exposure_name,
              weight, market_value, limit_weight, is_violation
            )
            VALUES ('default', ?, 'sector', 'Technology', 0.42, 42000, 0.35, TRUE)
            """,
            [AS_OF],
        )

        scores = compute_sector_theme_scores(
            conn,
            "run-1",
            portfolio_id="default",
            as_of_date=AS_OF,
        )

    tech = next(
        score
        for score in scores
        if score.exposure_type == "sector" and score.exposure_name == "Technology"
    )
    assert tech.current_weight == 0.42
    assert tech.limit_weight == 0.35
    assert tech.is_overexposed is True


def _seed_assets(conn) -> None:
    AssetRepository(conn).upsert_many(
        [
            Asset(
                "US_EQ_AAPL",
                "AAPL",
                "Apple Inc.",
                "equity",
                sector="Technology",
                industry="Consumer Electronics",
                metadata={"theme_tags": ["ai", "consumer"]},
            ),
            Asset(
                "US_EQ_NVDA",
                "NVDA",
                "NVIDIA Corp.",
                "equity",
                sector="Technology",
                industry="Semiconductors",
                metadata={"theme_tags": ["ai", "semiconductor"]},
            ),
            Asset(
                "US_EQ_META",
                "META",
                "Meta Platforms",
                "equity",
                sector="Communication Services",
                industry="Internet Content",
                metadata={"theme_tags": ["social"]},
            ),
        ]
    )


def _seed_candidates(conn) -> None:
    ScreeningRepository(conn).upsert_results(
        [
            ScreeningCandidate(
                run_id="run-1",
                asset_id="US_EQ_AAPL",
                score=0.9,
                rank=1,
                decision_bucket="candidate",
                reason="ranked by macro-adjusted factor score",
            ),
            ScreeningCandidate(
                run_id="run-1",
                asset_id="US_EQ_NVDA",
                score=0.6,
                rank=2,
                decision_bucket="watch",
                reason="ranked by macro-adjusted factor score",
            ),
            ScreeningCandidate(
                run_id="run-1",
                asset_id="US_EQ_META",
                score=0.4,
                rank=3,
                decision_bucket="watch",
                reason="ranked by macro-adjusted factor score",
            ),
        ]
    )
