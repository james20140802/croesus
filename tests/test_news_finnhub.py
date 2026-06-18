from datetime import date, datetime
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_news_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        items = {
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'news_items'"
            ).fetchall()
        }
        links = {
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'news_item_assets'"
            ).fetchall()
        }
    assert items == {
        "item_id", "source", "external_id", "url", "headline", "summary",
        "body", "published_at", "source_name", "category", "metadata", "created_at",
    }
    assert links == {"item_id", "asset_id", "relation"}
