from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_portfolio_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.duckdb"

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

    assert {
        "portfolios",
        "portfolio_holdings",
        "portfolio_snapshots",
        "portfolio_exposures",
        "policy_drifts",
    } <= tables
