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
