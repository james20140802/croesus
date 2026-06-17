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


def test_build_cik_map_pads_to_10_digits_and_uppercases() -> None:
    from croesus.disclosures.parse import build_cik_map

    payload = {
        "0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        "2": {"cik_str": None, "ticker": "BAD", "title": "no cik"},
        "3": {"cik_str": 111, "ticker": "", "title": "no ticker"},
        "4": {"cik_str": 0, "ticker": "ZERO", "title": "zero cik is invalid"},
    }
    assert build_cik_map(payload) == {
        "AAPL": "0000320193",
        "MSFT": "0000789019",
    }


def test_parse_recent_filings_drops_empty_form_when_unfiltered() -> None:
    from croesus.disclosures.parse import parse_recent_filings

    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["acc-1", "acc-2"],
                "filingDate": ["2026-06-01", "2026-06-02"],
                "reportDate": ["", ""],
                "form": ["", "8-K"],  # empty form must be dropped even with no filter
                "primaryDocument": ["a.htm", "b.htm"],
                "primaryDocDescription": ["", ""],
            }
        }
    }
    filings = parse_recent_filings(payload, cik="0000000001")
    assert [f.form_type for f in filings] == ["8-K"]


def _submissions_payload() -> dict:
    return {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-24-000123",
                    "0000320193-24-000120",
                    "0000320193-24-000115",
                ],
                "filingDate": ["2024-11-01", "2024-10-15", "2024-08-02"],
                "reportDate": ["2024-09-28", "2024-06-29", ""],
                "form": ["10-K", "4", "8-K"],
                "primaryDocument": ["aapl-20240928.htm", "form4.xml", "ex991.htm"],
                "primaryDocDescription": ["10-K", "FORM 4", ""],
            }
        },
    }


def test_parse_recent_filings_filters_forms_and_builds_url() -> None:
    from croesus.disclosures.parse import parse_recent_filings

    filings = parse_recent_filings(
        _submissions_payload(), cik="0000320193", forms={"10-K", "8-K"}
    )

    # The form-4 row is filtered out; newest-first order preserved.
    assert [f.form_type for f in filings] == ["10-K", "8-K"]

    tenk = filings[0]
    assert tenk.accession_number == "0000320193-24-000123"
    assert tenk.filed_date == date(2024, 11, 1)
    assert tenk.report_date == date(2024, 9, 28)
    # int(cik) strips leading zeros; accession dashes are stripped in the path.
    assert tenk.primary_doc_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019324000123/aapl-20240928.htm"
    )
    assert tenk.title == "10-K"

    eightk = filings[1]
    # Empty reportDate -> None; empty primaryDocDescription -> None (no synthesized
    # fallback, so a consumer can tell a real EDGAR title from a missing one).
    assert eightk.report_date is None
    assert eightk.title is None


def test_parse_recent_filings_no_form_filter_keeps_all_and_respects_limit() -> None:
    from croesus.disclosures.parse import parse_recent_filings

    all_filings = parse_recent_filings(_submissions_payload(), cik="0000320193")
    assert len(all_filings) == 3  # no filter -> form '4' kept

    limited = parse_recent_filings(_submissions_payload(), cik="0000320193", limit=1)
    assert len(limited) == 1
    assert limited[0].form_type == "10-K"


def test_parse_recent_filings_empty_payload_returns_empty() -> None:
    from croesus.disclosures.parse import parse_recent_filings

    assert parse_recent_filings({}, cik="0000320193") == []
    assert parse_recent_filings({"filings": {}}, cik="0000320193") == []


def test_disclosure_repository_upserts_idempotently(tmp_path: Path) -> None:
    from croesus.disclosures.models import Disclosure
    from croesus.disclosures.repository import DisclosureRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    first = Disclosure(
        asset_id="US_EQ_AAPL",
        accession_number="0000320193-24-000123",
        form_type="10-K",
        filed_date=date(2024, 11, 1),
        report_date=date(2024, 9, 28),
        primary_doc_url="https://example.com/a.htm",
        title="10-K",
    )

    with get_connection(db_path) as conn:
        repo = DisclosureRepository(conn)
        assert repo.upsert([first]) == 1
        # Re-ingest the same accession with a corrected title -> still one row.
        updated = Disclosure.from_raw(
            "US_EQ_AAPL",
            __import__("croesus.disclosures.models", fromlist=["RawFiling"]).RawFiling(
                accession_number="0000320193-24-000123",
                form_type="10-K",
                filed_date=date(2024, 11, 1),
                report_date=date(2024, 9, 28),
                primary_doc_url="https://example.com/a.htm",
                title="Annual Report",
            ),
        )
        assert repo.upsert([updated]) == 1

        rows = conn.execute(
            "SELECT asset_id, accession_number, title FROM disclosures"
        ).fetchall()
        assert rows == [("US_EQ_AAPL", "0000320193-24-000123", "Annual Report")]

        loaded = repo.load_for_asset("US_EQ_AAPL")
        assert len(loaded) == 1
        assert loaded[0].title == "Annual Report"
        assert loaded[0].source == "sec_edgar"


def test_ingest_disclosures_stores_filings_and_isolates_failures(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.disclosures.ingest import ingest_disclosures
    from croesus.disclosures.models import RawFiling

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    class FakeDisclosureSource:
        def fetch_recent_filings(self, symbol: str) -> list[RawFiling]:
            if symbol == "MSFT":
                raise RuntimeError("edgar unavailable")
            if symbol == "NVDA":
                return []  # known ticker, no matching filings
            return [
                RawFiling(
                    accession_number=f"acc-{symbol}-1",
                    form_type="8-K",
                    filed_date=date(2026, 6, 1),
                    report_date=None,
                    primary_doc_url=f"https://example.com/{symbol}.htm",
                    title="8-K",
                )
            ]

    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # seeds AAPL, MSFT, NVDA as US equities
        result = ingest_disclosures(conn, FakeDisclosureSource())
        stored = conn.execute(
            "SELECT asset_id, accession_number, form_type FROM disclosures ORDER BY asset_id"
        ).fetchall()

    assert result.succeeded == ["AAPL"]
    assert result.skipped == {"NVDA": "no filings returned"}
    assert result.failed == {"MSFT": "edgar unavailable"}
    assert stored == [("US_EQ_AAPL", "acc-AAPL-1", "8-K")]


def test_disclosures_registered_in_sync_pipeline() -> None:
    from croesus.jobs.local_sync import default_sync_jobs
    from croesus.jobs.run_status import DOMAINS_BY_NAME

    # Freshness domain exists and points at the disclosures job.
    assert "disclosures" in DOMAINS_BY_NAME
    assert DOMAINS_BY_NAME["disclosures"].job_name == "disclosures_run"

    jobs = {job.name: job for job in default_sync_jobs()}
    assert "disclosures_run" in jobs
    disclosures_job = jobs["disclosures_run"]
    assert disclosures_job.domains == ("disclosures",)
    # Needs the universe but must not be blocked by a universe-refresh failure.
    assert disclosures_job.soft_depends_on == ("universe_refresh",)
    assert disclosures_job.depends_on == ()


def test_parse_recent_filings_missing_primary_document_yields_none_url() -> None:
    from croesus.disclosures.parse import parse_recent_filings

    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-24-000123", "0000320193-24-000124"],
                "filingDate": ["2024-11-01", "2024-10-01"],
                "reportDate": ["2024-09-28", ""],
                "form": ["10-K", "8-K"],
                "primaryDocument": ["", None],
                "primaryDocDescription": ["10-K", "8-K"],
            }
        }
    }
    filings = parse_recent_filings(payload, cik="0000320193")
    assert len(filings) == 2
    # Empty string primaryDocument -> None URL.
    assert filings[0].primary_doc_url is None
    # None/absent primaryDocument -> None URL.
    assert filings[1].primary_doc_url is None


def test_build_cik_map_skips_non_numeric_cik() -> None:
    from croesus.disclosures.parse import build_cik_map

    payload = {
        "0": {"cik_str": "N/A", "ticker": "BAD", "title": "bad cik"},
        "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    }
    # Must not raise; bad entry is silently skipped.
    result = build_cik_map(payload)
    assert result == {"AAPL": "0000320193"}
    assert "BAD" not in result


def test_ingest_disclosures_aborts_when_cik_map_unavailable(tmp_path: Path) -> None:
    import pytest

    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.disclosures.ingest import ingest_disclosures
    from croesus.disclosures.source import CikMapUnavailableError

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    class BrokenSource:
        def fetch_recent_filings(self, symbol: str) -> list:
            raise CikMapUnavailableError("could not fetch EDGAR ticker->CIK map")

    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        with pytest.raises(CikMapUnavailableError):
            ingest_disclosures(conn, BrokenSource())
