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


def test_gdelt_source_satisfies_protocol_and_builds_params() -> None:
    from datetime import date

    from croesus.news.gdelt_source import GdeltDocSource, GdeltNewsSource

    source = GdeltDocSource()
    assert isinstance(source, GdeltNewsSource)
    assert source.name == "gdelt"
    # Pure param builder — no network.
    params = source.build_params('"Apple"', since=date(2026, 6, 1), until=date(2026, 6, 8))
    assert params["query"] == '"Apple" sourcelang:english'
    assert params["mode"] == "artlist"
    assert params["format"] == "json"
    assert params["startdatetime"] == "20260601000000"
    assert params["enddatetime"] == "20260608235959"  # end-of-day, not midnight


def test_article_body_fetcher_protocol() -> None:
    from croesus.news.body_fetch import ArticleBodyFetcher, TrafilaturaBodyFetcher

    fetcher = TrafilaturaBodyFetcher()
    assert isinstance(fetcher, ArticleBodyFetcher)


def test_trafilatura_body_fetcher_swallows_errors(monkeypatch) -> None:
    # A download/extract failure must yield None, never propagate — else one bad
    # URL aborts every article for that asset in the ingest loop.
    import sys
    import types

    from croesus.news.body_fetch import TrafilaturaBodyFetcher

    fake = types.ModuleType("trafilatura")

    def _boom(url, config=None):
        raise RuntimeError("network down")

    fake.fetch_url = _boom
    fake.extract = lambda *a, **k: None
    fake_settings = types.ModuleType("trafilatura.settings")

    class _Cfg:
        def set(self, *a) -> None:
            pass

    fake_settings.use_config = lambda: _Cfg()
    monkeypatch.setitem(sys.modules, "trafilatura", fake)
    monkeypatch.setitem(sys.modules, "trafilatura.settings", fake_settings)

    assert TrafilaturaBodyFetcher().fetch_body("https://x.com/a") is None


def test_ingest_gdelt_news_links_bodies_and_isolates(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.news.gdelt_ingest import ingest_gdelt_news
    from croesus.news.models import RawNewsArticle

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    class FakeGdeltSource:
        name = "gdelt"

        def fetch_articles(self, query_term, *, since, until):
            if "MSFT" in query_term or "Microsoft" in query_term:
                raise RuntimeError("gdelt unavailable")
            if "NVIDIA" in query_term or "NVDA" in query_term:
                return []
            return [RawNewsArticle(
                external_id=f"https://x.com/{query_term}", url=f"https://x.com/{query_term}",
                headline="h", summary=None, published_at=None,
                source_name="x.com", category=None, tickers=(),
            )]

    class FakeBodyFetcher:
        def fetch_body(self, url):
            return f"body for {url}"

    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL (Apple Inc.), MSFT (Microsoft...), NVDA (NVIDIA...)
        result = ingest_gdelt_news(conn, FakeGdeltSource(), FakeBodyFetcher())
        rows = conn.execute(
            "SELECT i.source, i.body, l.asset_id, l.relation "
            "FROM news_items i JOIN news_item_assets l ON l.item_id = i.item_id "
            "ORDER BY l.asset_id"
        ).fetchall()

    # Apple succeeded (article + body + link); Microsoft failed; NVIDIA empty.
    assert "AAPL" in result.scanned and "NVDA" in result.scanned
    assert "MSFT" in result.failed
    assert result.stored == 1
    assert len(rows) == 1
    source, body, asset_id, relation = rows[0]
    assert source == "gdelt"
    assert body.startswith("body for ")
    assert asset_id == "US_EQ_AAPL"
    assert relation == "queried"


def test_news_gdelt_registered_in_sync_pipeline() -> None:
    from croesus.jobs.local_sync import default_sync_jobs
    from croesus.jobs.run_status import DOMAINS_BY_NAME

    assert "news_gdelt" in DOMAINS_BY_NAME
    assert DOMAINS_BY_NAME["news_gdelt"].job_name == "news_gdelt_run"

    jobs = {job.name: job for job in default_sync_jobs()}
    assert "news_gdelt_run" in jobs
    job = jobs["news_gdelt_run"]
    assert job.domains == ("news_gdelt",)
    assert job.soft_depends_on == ("universe_refresh",)
