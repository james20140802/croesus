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


def test_news_models_and_item_id() -> None:
    from croesus.news.models import (
        NewsIngestionResult,
        RawNewsArticle,
        make_item_id,
    )

    # Deterministic + source-namespaced.
    assert make_item_id("finnhub", "12345") == make_item_id("finnhub", "12345")
    assert make_item_id("finnhub", "12345") != make_item_id("gdelt", "12345")
    assert len(make_item_id("finnhub", "12345")) == 40  # sha1 hex

    article = RawNewsArticle(
        external_id="12345",
        url="https://x.com/a",
        headline="Apple ships thing",
        summary="A summary.",
        published_at=datetime(2026, 6, 1, 12, 0, 0),
        source_name="Reuters",
        category="company news",
        tickers=("AAPL", "MSFT"),
    )
    assert article.tickers == ("AAPL", "MSFT")

    result = NewsIngestionResult()
    assert result.scanned == [] and result.stored == 0 and result.failed == {}
