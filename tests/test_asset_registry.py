from pathlib import Path

from croesus.assets.repository import AssetRepository
from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_sprint_001_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"

    migrate(db_path)

    with get_connection(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

    assert {"assets", "prices_daily", "factor_values", "screening_results"} <= tables


def test_seed_us_equities_is_idempotent_and_lists_active_assets(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        seed_us_equities(conn)
        assets = AssetRepository(conn).list_active(asset_type="equity", country="US")

    assert [asset.asset_id for asset in assets] == ["US_EQ_AAPL", "US_EQ_MSFT", "US_EQ_NVDA"]
    assert [asset.symbol for asset in assets] == ["AAPL", "MSFT", "NVDA"]
    assert all(asset.is_active for asset in assets)
