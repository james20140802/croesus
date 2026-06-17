from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_events_table(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'events'"
            ).fetchall()
        }
    assert cols == {
        "asset_id",
        "as_of_date",
        "event_type",
        "direction",
        "magnitude",
        "detail",
        "source",
        "created_at",
    }
