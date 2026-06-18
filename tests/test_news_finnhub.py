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


def test_parse_company_news_maps_fields_and_tickers() -> None:
    from croesus.news.parse import parse_company_news

    payload = [
        {
            "id": 7777,
            "headline": "Apple unveils X",
            "summary": "Apple did a thing.",
            "url": "https://r.com/apple-x",
            "source": "Reuters",
            "datetime": 1748779200,  # 2025-06-01 12:00:00 UTC
            "related": "AAPL,MSFT",
            "category": "company",
        },
        {  # missing id -> dropped (no stable external id)
            "headline": "no id",
            "url": "https://r.com/noid",
            "datetime": 1748779200,
        },
    ]
    articles = parse_company_news(payload, symbol="AAPL")
    assert len(articles) == 1
    a = articles[0]
    assert a.external_id == "7777"
    assert a.headline == "Apple unveils X"
    assert a.source_name == "Reuters"
    assert a.published_at.year == 2025 and a.published_at.month == 6
    # Queried symbol is first; related tickers follow, de-duplicated, uppercased.
    assert a.tickers[0] == "AAPL"
    assert set(a.tickers) == {"AAPL", "MSFT"}


def test_parse_company_news_empty_and_bad_rows() -> None:
    from croesus.news.parse import parse_company_news

    assert parse_company_news([], symbol="AAPL") == []
    # A row with id 0 (falsy) is dropped; a row with no datetime keeps published_at None.
    out = parse_company_news(
        [{"id": 0, "headline": "x"}, {"id": 9, "headline": "y", "related": ""}],
        symbol="NVDA",
    )
    assert [a.external_id for a in out] == ["9"]
    assert out[0].tickers == ("NVDA",)  # empty related -> just the queried symbol
    assert out[0].published_at is None
