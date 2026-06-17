# SEC EDGAR Disclosure Ingestion Implementation Plan (Phase B1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest recent SEC EDGAR filing metadata (10-K / 10-Q / 8-K) per US-equity asset into a new `disclosures` table, wired into the `local_sync` pipeline — the first half of the opportunity engine's candidate-sourcing funnel (spec Phase B).

**Architecture:** Mirror the existing `croesus/prices/` ingestion pattern exactly. A new `croesus/disclosures/` package holds: pure, network-free parsers (EDGAR JSON → records) that are fully unit-testable; an injectable `DisclosureSource` protocol with a network `EdgarDisclosureSource` implementation (so tests use a fake, just like `FakePriceSource`); a repository doing `ON CONFLICT` upserts; and an ingest job with per-asset error isolation. Phase B1 stores **filing metadata only** (form, dates, document URL) — no document-text download and no LLM. Storing the primary-document URL lets a later phase fetch text on demand.

**Tech Stack:** Python, DuckDB (via `croesus.db`), `requests` (already a dependency, used by the sentiment scraper), `pandas` only where already conventional (not needed here). No new third-party dependencies.

---

## Scope & Boundaries

- **In scope:** EDGAR ticker→CIK resolution, recent-filings fetch + parse, `disclosures` table, repository upsert, ingest job, `local_sync`/freshness wiring.
- **Out of scope (Phase B2, separate plan):** the event-driven pre-filter that *reads* `disclosures` (+ prices/valuations) to emit candidate events. This plan only *produces* the `disclosures` data.
- **Out of scope (later phases):** downloading/parsing filing **text**, any LLM use, news-API ingestion.
- **Universe filter:** US operating companies only — filter assets to `asset_type == "equity"`. ETFs/funds rarely file narrative 8-Ks; non-US filers (ADRs file 6-K/20-F) simply won't appear in EDGAR's `company_tickers.json` map and are skipped naturally.

## File Structure

| File | Responsibility |
|---|---|
| `croesus/disclosures/__init__.py` | Package marker (empty). |
| `croesus/disclosures/models.py` | `RawFiling` (source-shaped, no asset_id) and `Disclosure` (DB-shaped, with `asset_id`) frozen dataclasses + `Disclosure.from_raw`. |
| `croesus/disclosures/parse.py` | Pure, network-free parsers: `build_cik_map`, `parse_recent_filings`, and date/URL helpers. Fully unit-tested. |
| `croesus/disclosures/source.py` | `DisclosureSource` Protocol + network `EdgarDisclosureSource` (uses `requests`, calls the pure parsers). |
| `croesus/disclosures/repository.py` | `DisclosureRepository.upsert` / `load_for_asset`. |
| `croesus/disclosures/ingest.py` | `ingest_disclosures(conn, source=None, ...)` + `DisclosureIngestionResult`. Per-asset error isolation. |
| `croesus/db/schema.sql` | Append `disclosures` table DDL. |
| `croesus/jobs/run_status.py` | Add a `DomainSpec` for the `disclosures` domain. |
| `croesus/jobs/local_sync.py` | Add `_run_disclosures` runner + register the `SyncJob`. |
| `tests/test_disclosures.py` | All unit/integration tests for the above. |

---

### Task 1: `disclosures` table schema

**Files:**
- Modify: `croesus/db/schema.sql` (append at end of file)
- Test: `tests/test_disclosures.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_disclosures.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosures.py::test_migrate_creates_disclosures_table -v`
Expected: FAIL — the query returns an empty set because the table does not exist.

- [ ] **Step 3: Append the table DDL**

Append to the end of `croesus/db/schema.sql`:

```sql
-- Phase B1 (opportunity engine): SEC EDGAR filing metadata. One row per
-- (asset, accession). Stores filing METADATA only — form type, filing/report
-- dates, and the primary-document URL — never the document text or any LLM
-- output. This is the raw feed the event-driven pre-filter (Phase B2) scans for
-- "something forward just happened" triggers (e.g. a new 8-K). ``accession_number``
-- is EDGAR's globally unique filing id, so (asset_id, accession_number) is a
-- stable natural key for idempotent re-ingestion.
CREATE TABLE IF NOT EXISTS disclosures (
  asset_id          TEXT NOT NULL,
  accession_number  TEXT NOT NULL,
  form_type         TEXT NOT NULL,   -- '10-K' | '10-Q' | '8-K'
  filed_date        DATE NOT NULL,
  report_date       DATE,            -- period the filing reports on; may be absent
  primary_doc_url   TEXT,            -- URL to the primary document on sec.gov
  title             TEXT,            -- primaryDocDescription, falls back to form_type
  source            TEXT NOT NULL,   -- 'sec_edgar'
  created_at        TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, accession_number)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosures.py::test_migrate_creates_disclosures_table -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/db/schema.sql tests/test_disclosures.py
git commit -m "🗃️ chore: add disclosures table for SEC EDGAR filing metadata"
```

---

### Task 2: `RawFiling` and `Disclosure` models

**Files:**
- Create: `croesus/disclosures/__init__.py`
- Create: `croesus/disclosures/models.py`
- Test: `tests/test_disclosures.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosures.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosures.py::test_disclosure_from_raw_attaches_asset_id_and_default_source -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures'`

- [ ] **Step 3: Create the package and models**

Create `croesus/disclosures/__init__.py` (empty file).

Create `croesus/disclosures/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

DEFAULT_SOURCE = "sec_edgar"


@dataclass(frozen=True)
class RawFiling:
    """A filing as parsed from the source, before it is tied to an asset.

    Mirrors the columns of ``disclosures`` minus ``asset_id``/``source`` so the
    pure parser can produce these without knowing which Croesus asset they
    belong to (the ingest loop attaches that).
    """

    accession_number: str
    form_type: str
    filed_date: date
    report_date: date | None
    primary_doc_url: str | None
    title: str | None


@dataclass(frozen=True)
class Disclosure:
    """A filing tied to a Croesus asset, ready to persist to ``disclosures``."""

    asset_id: str
    accession_number: str
    form_type: str
    filed_date: date
    report_date: date | None
    primary_doc_url: str | None
    title: str | None
    source: str = DEFAULT_SOURCE

    @classmethod
    def from_raw(
        cls, asset_id: str, raw: RawFiling, *, source: str = DEFAULT_SOURCE
    ) -> "Disclosure":
        return cls(
            asset_id=asset_id,
            accession_number=raw.accession_number,
            form_type=raw.form_type,
            filed_date=raw.filed_date,
            report_date=raw.report_date,
            primary_doc_url=raw.primary_doc_url,
            title=raw.title,
            source=source,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosures.py::test_disclosure_from_raw_attaches_asset_id_and_default_source -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/__init__.py croesus/disclosures/models.py tests/test_disclosures.py
git commit -m "✨ feat: add RawFiling and Disclosure models for EDGAR ingestion"
```

---

### Task 3: Pure EDGAR parsers (`build_cik_map`, `parse_recent_filings`)

**Files:**
- Create: `croesus/disclosures/parse.py`
- Test: `tests/test_disclosures.py` (add)

These functions are network-free and take already-decoded JSON (Python dicts). They carry all the EDGAR-format knowledge and are the heart of the test coverage.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_disclosures.py`:

```python
def test_build_cik_map_pads_to_10_digits_and_uppercases() -> None:
    from croesus.disclosures.parse import build_cik_map

    payload = {
        "0": {"cik_str": 320193, "ticker": "aapl", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        "2": {"cik_str": None, "ticker": "BAD", "title": "no cik"},
        "3": {"cik_str": 111, "ticker": "", "title": "no ticker"},
    }
    assert build_cik_map(payload) == {
        "AAPL": "0000320193",
        "MSFT": "0000789019",
    }


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
                "reportDate": ["2024-09-28", "", "2024-06-29"],
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
    # Empty reportDate -> None; empty primaryDocDescription -> falls back to form.
    assert eightk.report_date is None
    assert eightk.title == "8-K"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_disclosures.py -k "build_cik_map or parse_recent_filings" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.parse'`

- [ ] **Step 3: Implement the parsers**

Create `croesus/disclosures/parse.py`:

```python
from __future__ import annotations

from datetime import date

from croesus.disclosures.models import RawFiling

# EDGAR submissions "recent" can hold up to ~1000 filings; we only want the most
# recent handful per name for the event funnel, so cap the parse.
DEFAULT_LIMIT = 40

_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"


def build_cik_map(company_tickers_payload: dict) -> dict[str, str]:
    """Map UPPER-case ticker -> zero-padded 10-digit CIK string.

    Input is the decoded ``company_tickers.json`` EDGAR publishes: a dict whose
    values are ``{"cik_str": int, "ticker": str, "title": str}``. Entries
    missing a ticker or CIK are skipped.
    """
    cik_map: dict[str, str] = {}
    for entry in company_tickers_payload.values():
        ticker = (entry.get("ticker") or "").upper()
        cik = entry.get("cik_str")
        if not ticker or cik is None:
            continue
        cik_map[ticker] = f"{int(cik):010d}"
    return cik_map


def parse_recent_filings(
    submissions_payload: dict,
    *,
    cik: str,
    forms: set[str] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[RawFiling]:
    """Parse EDGAR ``submissions`` JSON into ``RawFiling`` records, newest first.

    ``forms`` (e.g. ``{"10-K", "10-Q", "8-K"}``) filters by form type; ``None``
    keeps every form. Rows missing an accession number or a parseable filing
    date are dropped. Stops after ``limit`` kept rows.
    """
    recent = (submissions_payload.get("filings") or {}).get("recent") or {}
    accessions = recent.get("accessionNumber") or []
    filing_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    form_list = recent.get("form") or []
    documents = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []

    out: list[RawFiling] = []
    for i, accession in enumerate(accessions):
        form = form_list[i] if i < len(form_list) else None
        if form is None:
            continue
        if forms is not None and form not in forms:
            continue
        filed = _parse_date(filing_dates[i] if i < len(filing_dates) else None)
        if not accession or filed is None:
            continue
        report = _parse_date(report_dates[i] if i < len(report_dates) else None)
        document = documents[i] if i < len(documents) else None
        description = descriptions[i] if i < len(descriptions) else None
        out.append(
            RawFiling(
                accession_number=accession,
                form_type=form,
                filed_date=filed,
                report_date=report,
                primary_doc_url=_build_doc_url(cik, accession, document),
                title=description or form,
            )
        )
        if len(out) >= limit:
            break
    return out


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _build_doc_url(cik: str, accession: str, document: str | None) -> str | None:
    if not document:
        return None
    # The archive path uses the CIK with leading zeros stripped and the
    # accession number with its dashes removed.
    cik_int = int(cik)
    accession_nodashes = accession.replace("-", "")
    return f"{_ARCHIVE_BASE}/{cik_int}/{accession_nodashes}/{document}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_disclosures.py -k "build_cik_map or parse_recent_filings" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/parse.py tests/test_disclosures.py
git commit -m "✨ feat: add pure EDGAR submissions parsers (cik map + recent filings)"
```

---

### Task 4: `DisclosureRepository` upsert

**Files:**
- Create: `croesus/disclosures/repository.py`
- Test: `tests/test_disclosures.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosures.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosures.py::test_disclosure_repository_upserts_idempotently -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.repository'`

- [ ] **Step 3: Implement the repository**

Create `croesus/disclosures/repository.py`:

```python
from __future__ import annotations

import duckdb

from croesus.disclosures.models import Disclosure


class DisclosureRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, disclosures: list[Disclosure]) -> int:
        """Insert or update filings keyed by (asset_id, accession_number).

        Idempotent: re-ingesting the same accession overwrites the mutable
        fields rather than duplicating the row. Returns the number of rows
        written.
        """
        if not disclosures:
            return 0
        rows = [
            (
                d.asset_id,
                d.accession_number,
                d.form_type,
                d.filed_date,
                d.report_date,
                d.primary_doc_url,
                d.title,
                d.source,
            )
            for d in disclosures
        ]
        self.conn.executemany(
            """
            INSERT INTO disclosures (
              asset_id, accession_number, form_type, filed_date,
              report_date, primary_doc_url, title, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, accession_number) DO UPDATE SET
              form_type = excluded.form_type,
              filed_date = excluded.filed_date,
              report_date = excluded.report_date,
              primary_doc_url = excluded.primary_doc_url,
              title = excluded.title,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def load_for_asset(self, asset_id: str, *, limit: int = 50) -> list[Disclosure]:
        """Most-recent-first filings for one asset (used by the Phase B2 filter)."""
        result = self.conn.execute(
            """
            SELECT asset_id, accession_number, form_type, filed_date,
                   report_date, primary_doc_url, title, source
            FROM disclosures
            WHERE asset_id = ?
            ORDER BY filed_date DESC, accession_number DESC
            LIMIT ?
            """,
            [asset_id, limit],
        ).fetchall()
        return [
            Disclosure(
                asset_id=row[0],
                accession_number=row[1],
                form_type=row[2],
                filed_date=row[3],
                report_date=row[4],
                primary_doc_url=row[5],
                title=row[6],
                source=row[7],
            )
            for row in result
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosures.py::test_disclosure_repository_upserts_idempotently -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/repository.py tests/test_disclosures.py
git commit -m "✨ feat: add DisclosureRepository with idempotent upsert"
```

---

### Task 5: `DisclosureSource` protocol, `EdgarDisclosureSource`, and the ingest job

**Files:**
- Create: `croesus/disclosures/source.py`
- Create: `croesus/disclosures/ingest.py`
- Test: `tests/test_disclosures.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosures.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosures.py::test_ingest_disclosures_stores_filings_and_isolates_failures -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.ingest'`

- [ ] **Step 3: Implement the source protocol + EDGAR source**

Create `croesus/disclosures/source.py`:

```python
from __future__ import annotations

import os
from typing import Protocol

import requests

from croesus.disclosures.models import RawFiling
from croesus.disclosures.parse import build_cik_map, parse_recent_filings

# SEC requires a descriptive User-Agent with contact info; without one EDGAR
# returns 403. Overridable via env for deployment.
DEFAULT_USER_AGENT = "croesus research (drchasekim@gmail.com)"
DEFAULT_FORMS = frozenset({"10-K", "10-Q", "8-K"})

_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"


class DisclosureSource(Protocol):
    def fetch_recent_filings(self, symbol: str) -> list[RawFiling]:
        """Return recent filings for a ticker, newest first; empty if unknown."""


class EdgarDisclosureSource:
    """Fetches recent filing metadata from SEC EDGAR's public JSON API.

    The ticker->CIK map is fetched once and cached on the instance. All filing
    parsing is delegated to the pure functions in ``parse`` so this class only
    owns the HTTP concerns.
    """

    def __init__(
        self,
        user_agent: str | None = None,
        *,
        forms: frozenset[str] | None = DEFAULT_FORMS,
        limit: int = 40,
        timeout: float = 15.0,
    ) -> None:
        self._user_agent = user_agent or os.getenv(
            "CROESUS_SEC_USER_AGENT", DEFAULT_USER_AGENT
        )
        self._forms = set(forms) if forms is not None else None
        self._limit = limit
        self._timeout = timeout
        self._cik_map: dict[str, str] | None = None

    def fetch_recent_filings(self, symbol: str) -> list[RawFiling]:
        cik_map = self._ensure_cik_map()
        cik = cik_map.get(symbol.upper())
        if cik is None:
            return []
        resp = requests.get(
            _SUBMISSIONS_URL.format(cik=cik),
            headers=self._headers(),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return parse_recent_filings(
            resp.json(), cik=cik, forms=self._forms, limit=self._limit
        )

    def _ensure_cik_map(self) -> dict[str, str]:
        if self._cik_map is None:
            resp = requests.get(
                _TICKER_MAP_URL, headers=self._headers(), timeout=self._timeout
            )
            resp.raise_for_status()
            self._cik_map = build_cik_map(resp.json())
        return self._cik_map

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}
```

- [ ] **Step 4: Implement the ingest job**

Create `croesus/disclosures/ingest.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.disclosures.models import Disclosure
from croesus.disclosures.repository import DisclosureRepository
from croesus.disclosures.source import DisclosureSource, EdgarDisclosureSource

# US operating companies are the EDGAR filers we care about. ETFs/funds rarely
# file the narrative 8-Ks the event funnel keys on, and non-US filers won't be
# in EDGAR's ticker->CIK map (so they skip naturally), but excluding non-equity
# types up front avoids pointless network calls.
FILER_ASSET_TYPES = ("equity",)


@dataclass(frozen=True)
class DisclosureIngestionResult:
    succeeded: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def ingest_disclosures(
    conn: duckdb.DuckDBPyConnection,
    source: DisclosureSource | None = None,
    *,
    log: Callable[[str], None] = print,
) -> DisclosureIngestionResult:
    """Fetch and upsert recent SEC filings for every active US-equity asset.

    Per-asset failures are recorded and skipped so one unreachable filer never
    stops the run (mirrors ``ingest_daily_prices``).
    """
    source = source or EdgarDisclosureSource()
    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES
    ]
    repo = DisclosureRepository(conn)
    result = DisclosureIngestionResult()

    for asset in assets:
        try:
            raw_filings = source.fetch_recent_filings(asset.symbol)
            if not raw_filings:
                result.skipped[asset.symbol] = "no filings returned"
                log(f"skip {asset.symbol}: no filings returned")
                continue
            disclosures = [
                Disclosure.from_raw(asset.asset_id, raw) for raw in raw_filings
            ]
            rows = repo.upsert(disclosures)
            result.succeeded.append(asset.symbol)
            log(f"stored {rows} disclosures for {asset.symbol}")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_disclosures.py::test_ingest_disclosures_stores_filings_and_isolates_failures -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add croesus/disclosures/source.py croesus/disclosures/ingest.py tests/test_disclosures.py
git commit -m "✨ feat: add EDGAR disclosure source and ingest job"
```

---

### Task 6: Wire into `local_sync` and freshness tracking

**Files:**
- Modify: `croesus/jobs/run_status.py` (add a `DomainSpec` to `DOMAIN_REGISTRY`, ~line 149, before the closing `)`)
- Modify: `croesus/jobs/local_sync.py` (add `_run_disclosures` runner near the other `_run_*` functions, ~line 318; register a `SyncJob` in `default_sync_jobs()`, ~line 348)
- Test: `tests/test_disclosures.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosures.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosures.py::test_disclosures_registered_in_sync_pipeline -v`
Expected: FAIL — `"disclosures" not in DOMAINS_BY_NAME` (KeyError/assertion).

- [ ] **Step 3: Add the freshness domain**

In `croesus/jobs/run_status.py`, inside the `DOMAIN_REGISTRY` tuple, add this entry immediately after the `asset_universe` `DomainSpec` (i.e. just before the tuple's closing `)` near line 149):

```python
    # SEC filings arrive irregularly (quarterly 10-K/10-Q plus event-driven
    # 8-Ks). A ~daily refresh threshold keeps new 8-Ks flowing into the event
    # funnel promptly; MAX(filed_date) lags over weekends/holidays, which simply
    # marks the domain due and triggers a (cheap, mostly no-op) refresh.
    DomainSpec(
        "disclosures", "disclosures_run", 48.0,
        lambda c: _scalar_date(c, "SELECT MAX(filed_date) FROM disclosures"),
    ),
```

- [ ] **Step 4: Add the runner**

In `croesus/jobs/local_sync.py`, add this function next to the other `_run_*` runners (e.g. after `_run_universe_refresh`, around line 318):

```python
def _run_disclosures(db: Path) -> str:
    from croesus.disclosures.ingest import ingest_disclosures

    with get_connection(db) as conn:
        result = ingest_disclosures(conn)
    return (
        f"disclosures ok={len(result.succeeded)} "
        f"skip={len(result.skipped)} fail={len(result.failed)}"
    )
```

- [ ] **Step 5: Register the job**

In `croesus/jobs/local_sync.py`, in `default_sync_jobs()`, add this `SyncJob` to the returned list immediately after the `universe_refresh` entry:

```python
        SyncJob(
            "disclosures_run", ("disclosures",), _run_disclosures,
            soft_depends_on=("universe_refresh",),
        ),
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_disclosures.py::test_disclosures_registered_in_sync_pipeline -v`
Expected: PASS

- [ ] **Step 7: Run the full disclosures suite and the sync-status suite**

Run: `pytest tests/test_disclosures.py -v`
Expected: PASS (all tests)

Run: `pytest tests/ -k "sync or run_status" -v`
Expected: PASS — confirms the new `DomainSpec`/`SyncJob` did not break existing freshness/orchestration tests.

- [ ] **Step 8: Commit**

```bash
git add croesus/jobs/run_status.py croesus/jobs/local_sync.py tests/test_disclosures.py
git commit -m "✨ feat: wire disclosures ingestion into local_sync pipeline"
```

---

### Task 7: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -q`
Expected: PASS — all pre-existing tests (481 at Phase A) plus the new disclosures tests are green.

- [ ] **Step 2: Confirm no stray files / lint**

Run: `git status --short`
Expected: clean working tree (everything committed).

---

## Self-Review (controller checklist — done while writing this plan)

**1. Spec coverage (spec §Architecture "후보 소싱" + Phase B):**
- "공통 수집(EDGAR 우선)" → Tasks 1–5 (table, models, parsers, repo, EDGAR source + ingest). ✅
- "10-K/10-Q/8-K, XBRL" → `DEFAULT_FORMS = {"10-K","10-Q","8-K"}` (XBRL financial facts are out of scope for B1 — fundamentals already come via `quarterly_run`; this funnel only needs filing *events*). ✅ (documented limit, not a gap)
- "후보 소싱 깔때기" deterministic, no LLM → entire plan is deterministic metadata; no LLM anywhere. ✅
- The **event pre-filter** ("이벤트·이상 트리거") is intentionally **Phase B2** (separate plan) — this plan produces the `disclosures` feed it will consume (`load_for_asset` is provided for it). ✅ (scope split stated up front)

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete code. ✅

**3. Type consistency:** `RawFiling`/`Disclosure` fields are identical across Tasks 2–5; `Disclosure.from_raw(asset_id, raw)` signature matches every call site; `DisclosureSource.fetch_recent_filings(symbol) -> list[RawFiling]` matches the fake in Task 5 and `EdgarDisclosureSource`; `DisclosureIngestionResult` fields (`succeeded`/`skipped`/`failed`) match the Task 5 assertions; `DomainSpec("disclosures", "disclosures_run", ...)` job_name matches the `SyncJob("disclosures_run", ...)` name (Task 6). ✅
