from datetime import datetime
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_repository_persists_article_body(tmp_path: Path) -> None:
    from croesus.news.models import RawNewsArticle
    from croesus.news.repository import NewsRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    art = RawNewsArticle(
        external_id="https://x.com/a", url="https://x.com/a", headline="H",
        summary=None, published_at=datetime(2026, 6, 1, 12, 0, 0),
        source_name="reuters.com", category=None, tickers=("AAPL",),
        body="Full article body text.",
    )
    with get_connection(db_path) as conn:
        NewsRepository(conn).upsert_articles(
            "gdelt", [art], symbol_to_asset={"AAPL": "US_EQ_AAPL"}
        )
        body = conn.execute("SELECT body FROM news_items").fetchone()[0]
    assert body == "Full article body text."
