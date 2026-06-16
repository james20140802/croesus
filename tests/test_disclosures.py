from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_disclosures_table(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'disclosures'"
            ).fetchall()
        }
    assert cols == {
        "asset_id",
        "accession_number",
        "form_type",
        "filed_date",
        "report_date",
        "primary_doc_url",
        "title",
        "source",
        "created_at",
    }


def test_disclosure_from_raw_attaches_asset_id_and_default_source() -> None:
    from croesus.disclosures.models import Disclosure, RawFiling

    raw = RawFiling(
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filed_date=date(2024, 11, 1),
        report_date=date(2024, 9, 28),
        primary_doc_url="https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl.htm",
        title="10-K",
    )
    disclosure = Disclosure.from_raw("US_EQ_AAPL", raw)

    assert disclosure.asset_id == "US_EQ_AAPL"
    assert disclosure.accession_number == "0000320193-24-000123"
    assert disclosure.form_type == "10-K"
    assert disclosure.filed_date == date(2024, 11, 1)
    assert disclosure.report_date == date(2024, 9, 28)
    assert disclosure.primary_doc_url.endswith("aapl.htm")
    assert disclosure.title == "10-K"
    assert disclosure.source == "sec_edgar"
