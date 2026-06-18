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
