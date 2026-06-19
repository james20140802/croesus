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


def test_company_query_term_strips_suffixes_and_quotes() -> None:
    from croesus.news.gdelt_parse import company_query_term

    assert company_query_term("Apple Inc.") == '"Apple"'
    assert company_query_term("Microsoft Corporation") == '"Microsoft"'
    assert company_query_term("Alphabet Inc. Class A") == '"Alphabet"'
    assert company_query_term("NVIDIA Corp") == '"NVIDIA"'
    # No usable name -> empty string (caller skips).
    assert company_query_term("") == ""
    assert company_query_term(None) == ""


def test_parse_gdelt_doc_maps_articles() -> None:
    from croesus.news.gdelt_parse import parse_gdelt_doc

    payload = {
        "articles": [
            {
                "url": "https://reuters.com/x",
                "title": "Apple wins approval",
                "seendate": "20260601T120000Z",
                "domain": "reuters.com",
                "language": "English",
                "sourcecountry": "US",
            },
            {"title": "no url -> dropped", "seendate": "20260601T120000Z"},
        ]
    }
    articles = parse_gdelt_doc(payload)
    assert len(articles) == 1
    a = articles[0]
    assert a.external_id == "https://reuters.com/x"
    assert a.url == "https://reuters.com/x"
    assert a.headline == "Apple wins approval"
    assert a.source_name == "reuters.com"
    assert a.published_at.year == 2026 and a.published_at.month == 6
    assert a.tickers == ()       # mapping is attached by the ingest job
    assert a.body is None        # body fetched separately


def test_parse_gdelt_doc_empty_and_missing_articles_key() -> None:
    from croesus.news.gdelt_parse import parse_gdelt_doc

    assert parse_gdelt_doc({}) == []
    assert parse_gdelt_doc({"articles": []}) == []
