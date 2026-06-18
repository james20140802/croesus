from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_disclosure_texts_table(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'disclosure_texts'"
            ).fetchall()
        }
    assert cols == {
        "asset_id",
        "accession_number",
        "source_url",
        "char_count",
        "text",
        "status",
        "source",
        "created_at",
    }


def test_disclosure_text_model_and_result() -> None:
    from croesus.disclosures.text_models import (
        DisclosureText,
        DisclosureTextIngestionResult,
    )

    text = DisclosureText(
        asset_id="US_EQ_AAPL",
        accession_number="0000320193-24-000123",
        source_url="https://www.sec.gov/Archives/edgar/data/320193/x/aapl.htm",
        char_count=11,
        text="Hello world",
        status="fetched",
    )
    assert text.asset_id == "US_EQ_AAPL"
    assert text.status == "fetched"
    assert text.source == "sec_edgar"  # default

    result = DisclosureTextIngestionResult()
    assert result.fetched == []
    assert result.skipped == []
    assert result.failed == {}


def test_extract_filing_text_strips_tags_scripts_and_whitespace() -> None:
    from croesus.disclosures.text_extract import extract_filing_text

    html = (
        "<html><head><style>p{color:red}</style></head>"
        "<body><p>Item 1.  Business</p>"
        "<script>trackUser()</script>"
        "<p>We make\n\n  phones.</p></body></html>"
    )
    text = extract_filing_text(html)
    # Tags gone; script/style content gone; whitespace collapsed to single spaces.
    assert text == "Item 1. Business We make phones."
    assert "trackUser" not in text
    assert "color:red" not in text


def test_extract_filing_text_empty_and_nonhtml_inputs() -> None:
    from croesus.disclosures.text_extract import extract_filing_text

    assert extract_filing_text("") == ""
    assert extract_filing_text("   \n  ") == ""
    # Plain text (no tags) is returned as-is (normalized).
    assert extract_filing_text("Just plain words") == "Just plain words"


def test_extract_filing_text_caps_length() -> None:
    from croesus.disclosures.text_extract import extract_filing_text

    html = "<p>" + ("x" * 100) + "</p>"
    assert extract_filing_text(html, max_chars=10) == "x" * 10


def test_edgar_document_source_satisfies_protocol() -> None:
    from croesus.disclosures.text_source import (
        DisclosureTextSource,
        EdgarDocumentSource,
    )

    source = EdgarDocumentSource(user_agent="test-agent (x@y.com)")
    # Structural typing: the concrete source satisfies the Protocol.
    assert isinstance(source, DisclosureTextSource)
    # The header carries the configured UA (SEC requires a contact UA).
    headers = source._headers()
    assert headers["User-Agent"] == "test-agent (x@y.com)"


def test_disclosure_text_repository_upsert_and_lookup(tmp_path: Path) -> None:
    from croesus.disclosures.text_models import DisclosureText
    from croesus.disclosures.text_repository import DisclosureTextRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    first = DisclosureText(
        asset_id="US_EQ_AAPL",
        accession_number="acc-1",
        source_url="https://example.com/a.htm",
        char_count=5,
        text="alpha",
        status="fetched",
    )
    with get_connection(db_path) as conn:
        repo = DisclosureTextRepository(conn)
        assert repo.upsert([first]) == 1
        assert repo.accessions_with_text("US_EQ_AAPL") == {"acc-1"}

        # Re-ingest same accession with new text -> still one row, updated.
        updated = DisclosureText(
            asset_id="US_EQ_AAPL", accession_number="acc-1",
            source_url="https://example.com/a.htm", char_count=4, text="beta",
            status="fetched",
        )
        assert repo.upsert([updated]) == 1
        got = repo.get("US_EQ_AAPL", "acc-1")
        assert got is not None
        assert got.text == "beta"
        assert got.char_count == 4

        # An 'empty'/'failed' row does NOT count as having usable text.
        repo.upsert([DisclosureText(
            asset_id="US_EQ_AAPL", accession_number="acc-2", source_url=None,
            char_count=0, text="", status="empty",
        )])
        assert repo.accessions_with_text("US_EQ_AAPL") == {"acc-1"}


def test_ingest_disclosure_texts_fetches_skips_and_isolates(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.disclosures.models import Disclosure
    from croesus.disclosures.repository import DisclosureRepository
    from croesus.disclosures.text_ingest import ingest_disclosure_texts
    from croesus.disclosures.text_models import DisclosureText
    from croesus.disclosures.text_repository import DisclosureTextRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    def _disc(asset_id: str, acc: str, url: str | None) -> Disclosure:
        return Disclosure(
            asset_id=asset_id, accession_number=acc, form_type="8-K",
            filed_date=date(2026, 6, 1), report_date=None,
            primary_doc_url=url, title=None,
        )

    class FakeDocSource:
        def fetch_document(self, url: str) -> str:
            if "boom" in url:
                raise RuntimeError("doc unavailable")
            return f"<html><body><p>Body for {url}</p></body></html>"

    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL, MSFT, NVDA
        DisclosureRepository(conn).upsert([
            _disc("US_EQ_AAPL", "aapl-1", "https://sec.gov/aapl1.htm"),  # already has text
            _disc("US_EQ_AAPL", "aapl-new", "https://sec.gov/aapl2.htm"),  # to fetch
            _disc("US_EQ_AAPL", "aapl-nourl", None),                       # no URL -> ignored
            _disc("US_EQ_MSFT", "msft-boom", "https://sec.gov/boom.htm"),  # fetch raises
        ])
        # aapl-1 text already exists -> must be skipped (not refetched).
        DisclosureTextRepository(conn).upsert([
            DisclosureText(
                asset_id="US_EQ_AAPL", accession_number="aapl-1",
                source_url="https://sec.gov/aapl1.htm", char_count=3, text="old",
                status="fetched",
            )
        ])

        result = ingest_disclosure_texts(conn, FakeDocSource())
        stored = conn.execute(
            "SELECT asset_id, accession_number, status FROM disclosure_texts "
            "ORDER BY asset_id, accession_number"
        ).fetchall()

    assert result.fetched == ["aapl-new"]                 # the one new URL'd filing
    assert result.skipped == ["aapl-1"]                   # already had text
    assert result.failed == {"msft-boom": "doc unavailable"}
    # aapl-1 untouched; aapl-new fetched; msft failure recorded; no-URL filing absent.
    assert ("US_EQ_AAPL", "aapl-1", "fetched") in stored
    assert ("US_EQ_AAPL", "aapl-new", "fetched") in stored
    assert ("US_EQ_MSFT", "msft-boom", "failed") in stored
    assert all(acc != "aapl-nourl" for _, acc, _ in stored)


def test_ingest_empty_filing_is_terminal_not_refetched(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.disclosures.models import Disclosure
    from croesus.disclosures.repository import DisclosureRepository
    from croesus.disclosures.text_ingest import ingest_disclosure_texts

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    class CountingEmptySource:
        def __init__(self) -> None:
            self.calls = 0

        def fetch_document(self, url: str) -> str:
            self.calls += 1
            return "<html><body></body></html>"  # valid HTML, no extractable text

    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        DisclosureRepository(conn).upsert([
            Disclosure(
                asset_id="US_EQ_AAPL", accession_number="aapl-empty", form_type="8-K",
                filed_date=date(2026, 6, 1), report_date=None,
                primary_doc_url="https://sec.gov/empty.htm", title=None,
            )
        ])
        source = CountingEmptySource()

        first = ingest_disclosure_texts(conn, source)
        # Fetched once, stored as 'empty' (not 'fetched'), so not in `fetched`.
        assert first.fetched == [] and first.skipped == []
        assert source.calls == 1

        second = ingest_disclosure_texts(conn, source)
        # 'empty' is terminal: the second run skips it and does NOT refetch.
        assert second.skipped == ["aapl-empty"]
        assert source.calls == 1


def test_ingest_defers_filings_past_limit_per_asset(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.disclosures.models import Disclosure
    from croesus.disclosures.repository import DisclosureRepository
    from croesus.disclosures.text_ingest import ingest_disclosure_texts

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    class FakeDocSource:
        def fetch_document(self, url: str) -> str:
            return f"<html><body><p>Body {url}</p></body></html>"

    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        # Two URL'd filings, budget of 1 -> one fetched, one deferred.
        DisclosureRepository(conn).upsert([
            Disclosure(
                asset_id="US_EQ_AAPL", accession_number="aapl-a", form_type="8-K",
                filed_date=date(2026, 6, 2), report_date=None,
                primary_doc_url="https://sec.gov/a.htm", title=None,
            ),
            Disclosure(
                asset_id="US_EQ_AAPL", accession_number="aapl-b", form_type="8-K",
                filed_date=date(2026, 6, 1), report_date=None,
                primary_doc_url="https://sec.gov/b.htm", title=None,
            ),
        ])
        result = ingest_disclosure_texts(conn, FakeDocSource(), limit_per_asset=1)

    # load_for_asset orders newest filed_date first -> aapl-a fetched, aapl-b deferred.
    assert result.fetched == ["aapl-a"]
    assert result.deferred == ["aapl-b"]


def test_disclosure_texts_registered_in_sync_pipeline() -> None:
    from croesus.jobs.local_sync import default_sync_jobs
    from croesus.jobs.run_status import DOMAINS_BY_NAME

    assert "disclosure_texts" in DOMAINS_BY_NAME
    assert DOMAINS_BY_NAME["disclosure_texts"].job_name == "disclosure_texts_run"

    jobs = {job.name: job for job in default_sync_jobs()}
    assert "disclosure_texts_run" in jobs
    job = jobs["disclosure_texts_run"]
    assert job.domains == ("disclosure_texts",)
    assert job.depends_on == ("disclosures_run",)
