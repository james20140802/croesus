# SEC Filing-Text Ingestion Implementation Plan (Phase C1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch the body text of SEC filings (10-K / 10-Q / 8-K) from the `primary_doc_url` already stored in `disclosures` (Phase B1), extract clean plain text, and persist it — the textual evidence base the Phase C2 structural-thesis grader will read.

**Architecture:** Mirror the Phase B1 `croesus/disclosures/` ingestion shape exactly. A pure, network-free `extract_filing_text(html)` (using `lxml.html`, an already-declared dependency) is fully unit-testable. An injectable `DisclosureTextSource` protocol with a network `EdgarDocumentSource` (reusing the EDGAR `User-Agent` discipline from `EdgarDisclosureSource`) lets tests use a fake. A repository upserts text keyed to `(asset_id, accession_number)`; an ingest job fetches per filing with idempotent skip (don't refetch stored text) and per-filing error isolation. Wired into `local_sync` after `disclosures_run`.

**Tech Stack:** Python, DuckDB (`croesus.db`), `requests` + `lxml` (both already declared dependencies). No new third-party dependencies.

---

## Scope & Boundaries

- **In scope:** fetch the document at each filing's `primary_doc_url`, strip it to clean plain text, store it (capped length + `char_count` + status), idempotently and with per-filing isolation; freshness/`local_sync` wiring.
- **Deliberately out of scope (later sub-phases):**
  - **C2** — the LLM structural-thesis grader that *reads* this text.
  - **C3** — grades→DcfKnobs mapping + bear/base/bull band.
  - **Section-aware extraction** (Item 1 Business / Item 1A Risk Factors / MD&A): v1 stores whitespace-normalized full text (capped); C2 handles sectioning/chunking when assembling the prompt. Noted as a future enhancement.
  - **Rate-limit pacing:** like B1, the current universe is tiny; sequential fetches with a generous timeout are fine. A token-bucket is a documented follow-up if the universe scales.
- **Bounding:** fetch at most `limit_per_asset` most-recent filings per active equity that have a URL and lack stored text. The funnel principle ("deep work on a shortlist") is honored by keeping the per-asset bound small and skipping already-fetched filings; a future step can pass only event-flagged `asset_ids`.

## Design Decisions (owned defaults)

| Decision | Choice | Rationale |
|---|---|---|
| HTML parsing | `lxml.html` (declared dep) | Robust real HTML parser; no new dependency; `lxml>=4.9` already in `pyproject.toml`. |
| Text shape | whitespace-normalized plain text, `script`/`style` dropped | Cheap, deterministic, good enough for LLM input; richer sectioning deferred to C2. |
| Length cap | `MAX_TEXT_CHARS = 1_000_000` | Bounds pathological filings in DuckDB while keeping a full 10-K body; C2 chunks for the model context. |
| Natural key | `(asset_id, accession_number)` | Matches the `disclosures` PK — one text row per filing; idempotent re-ingest. |
| Which filings | most-recent `limit_per_asset` (default 3) per active equity, forms `{10-K,10-Q,8-K}`, missing text only | Bounded cost; idempotent; thesis-relevant forms. |

## File Structure

| File | Responsibility |
|---|---|
| `croesus/disclosures/text_models.py` | `DisclosureText` + `DisclosureTextIngestionResult` dataclasses. |
| `croesus/disclosures/text_extract.py` | Pure `extract_filing_text(html, *, max_chars)` (lxml). Fully unit-tested. |
| `croesus/disclosures/text_source.py` | `DisclosureTextSource` Protocol + network `EdgarDocumentSource`. |
| `croesus/disclosures/text_repository.py` | `DisclosureTextRepository` (upsert / get / accessions_with_text). |
| `croesus/disclosures/text_ingest.py` | `ingest_disclosure_texts(conn, source, ...)` + per-filing isolation/idempotency. |
| `croesus/db/schema.sql` | Append `disclosure_texts` table. |
| `croesus/jobs/run_status.py` | Add a `DomainSpec` for the `disclosure_texts` domain. |
| `croesus/jobs/local_sync.py` | Add `_run_disclosure_texts` runner + register the `SyncJob`. |
| `tests/test_disclosure_texts.py` | All unit/integration tests. |

---

### Task 1: `disclosure_texts` table schema

**Files:**
- Modify: `croesus/db/schema.sql` (append at end)
- Test: `tests/test_disclosure_texts.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_disclosure_texts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosure_texts.py::test_migrate_creates_disclosure_texts_table -v`
Expected: FAIL — empty column set (table absent).

- [ ] **Step 3: Append the table DDL**

Append to the end of `croesus/db/schema.sql`:

```sql
-- Phase C1 (opportunity engine): SEC filing BODY TEXT. One row per
-- (asset, accession), holding the cleaned plain text of the filing's primary
-- document (fetched from disclosures.primary_doc_url). This is the textual
-- evidence the structural-thesis grader (Phase C2) reads — it stores extracted
-- text only, never an LLM judgement. ``status`` is 'fetched' | 'empty' | 'failed';
-- ``char_count`` is the stored text length (text is capped to bound DB size).
CREATE TABLE IF NOT EXISTS disclosure_texts (
  asset_id          TEXT NOT NULL,
  accession_number  TEXT NOT NULL,
  source_url        TEXT,
  char_count        INTEGER,
  text              TEXT,
  status            TEXT NOT NULL,   -- 'fetched' | 'empty' | 'failed'
  source            TEXT NOT NULL,   -- 'sec_edgar'
  created_at        TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, accession_number)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosure_texts.py::test_migrate_creates_disclosure_texts_table -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/db/schema.sql tests/test_disclosure_texts.py
git commit -m "🗃️ chore: add disclosure_texts table for SEC filing body text"
```

---

### Task 2: `DisclosureText` model + result

**Files:**
- Create: `croesus/disclosures/text_models.py`
- Test: `tests/test_disclosure_texts.py` (add)

(The `croesus/disclosures/__init__.py` package marker already exists from Phase B1.)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosure_texts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosure_texts.py::test_disclosure_text_model_and_result -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.text_models'`

- [ ] **Step 3: Create the models**

Create `croesus/disclosures/text_models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_SOURCE = "sec_edgar"

# Filing-text status values.
STATUS_FETCHED = "fetched"   # non-empty text extracted and stored
STATUS_EMPTY = "empty"       # fetched but no extractable text
STATUS_FAILED = "failed"     # fetch/extract raised (recorded for audit)


@dataclass(frozen=True)
class DisclosureText:
    """The cleaned body text of one filing, keyed to its disclosure."""

    asset_id: str
    accession_number: str
    source_url: str | None
    char_count: int
    text: str
    status: str
    source: str = DEFAULT_SOURCE


@dataclass(frozen=True)
class DisclosureTextIngestionResult:
    fetched: list[str] = field(default_factory=list)      # accession numbers fetched
    skipped: list[str] = field(default_factory=list)      # accession numbers already stored
    failed: dict[str, str] = field(default_factory=dict)  # accession number -> error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosure_texts.py::test_disclosure_text_model_and_result -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/text_models.py tests/test_disclosure_texts.py
git commit -m "✨ feat: add DisclosureText model for filing-text ingestion"
```

---

### Task 3: Pure `extract_filing_text` (lxml)

**Files:**
- Create: `croesus/disclosures/text_extract.py`
- Test: `tests/test_disclosure_texts.py` (add)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_disclosure_texts.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_disclosure_texts.py -k extract -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.text_extract'`

- [ ] **Step 3: Implement the extractor**

Create `croesus/disclosures/text_extract.py`:

```python
from __future__ import annotations

import re

import lxml.etree
import lxml.html

# Filing bodies (esp. 10-Ks) can be very large; cap stored text to bound DB size.
# The Phase C2 grader chunks/sections this for the model context.
MAX_TEXT_CHARS = 1_000_000

_WHITESPACE = re.compile(r"\s+")


def extract_filing_text(html: str, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Strip an HTML filing to clean, whitespace-normalized plain text.

    Drops ``script``/``style`` content, collapses runs of whitespace to single
    spaces, and caps the result at ``max_chars``. Returns ``""`` for empty or
    unparseable input. Pure and network-free.
    """
    if not html or not html.strip():
        return ""
    try:
        doc = lxml.html.fromstring(html)
    except (lxml.etree.ParserError, ValueError):
        return ""
    for element in doc.xpath("//script | //style"):
        element.drop_tree()
    text = _WHITESPACE.sub(" ", doc.text_content()).strip()
    return text[:max_chars]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_disclosure_texts.py -k extract -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/text_extract.py tests/test_disclosure_texts.py
git commit -m "✨ feat: add pure lxml filing-text extractor"
```

---

### Task 4: `DisclosureTextSource` protocol + `EdgarDocumentSource`

**Files:**
- Create: `croesus/disclosures/text_source.py`
- Test: `tests/test_disclosure_texts.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosure_texts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosure_texts.py::test_edgar_document_source_satisfies_protocol -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.text_source'`

- [ ] **Step 3: Implement the source**

Create `croesus/disclosures/text_source.py`:

```python
from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import requests

from croesus.disclosures.source import DEFAULT_USER_AGENT


@runtime_checkable
class DisclosureTextSource(Protocol):
    def fetch_document(self, url: str) -> str:
        """Return the raw document (HTML/text) at ``url``."""


class EdgarDocumentSource:
    """Fetches a filing's primary document over HTTP from sec.gov.

    Reuses the SEC ``User-Agent`` discipline from ``EdgarDisclosureSource``
    (EDGAR returns 403 without a descriptive contact UA).
    """

    def __init__(self, user_agent: str | None = None, *, timeout: float = 30.0) -> None:
        self._user_agent = user_agent or os.getenv(
            "CROESUS_SEC_USER_AGENT", DEFAULT_USER_AGENT
        )
        self._timeout = timeout

    def fetch_document(self, url: str) -> str:
        resp = requests.get(url, headers=self._headers(), timeout=self._timeout)
        resp.raise_for_status()
        return resp.text

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosure_texts.py::test_edgar_document_source_satisfies_protocol -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/text_source.py tests/test_disclosure_texts.py
git commit -m "✨ feat: add DisclosureTextSource protocol and EdgarDocumentSource"
```

---

### Task 5: `DisclosureTextRepository`

**Files:**
- Create: `croesus/disclosures/text_repository.py`
- Test: `tests/test_disclosure_texts.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosure_texts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosure_texts.py::test_disclosure_text_repository_upsert_and_lookup -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.text_repository'`

- [ ] **Step 3: Implement the repository**

Create `croesus/disclosures/text_repository.py`:

```python
from __future__ import annotations

import duckdb

from croesus.disclosures.text_models import STATUS_FETCHED, DisclosureText


class DisclosureTextRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, texts: list[DisclosureText]) -> int:
        """Insert/update filing texts keyed by (asset_id, accession_number).

        Idempotent; returns the number of rows submitted.
        """
        if not texts:
            return 0
        rows = [
            (
                t.asset_id,
                t.accession_number,
                t.source_url,
                t.char_count,
                t.text,
                t.status,
                t.source,
            )
            for t in texts
        ]
        self.conn.executemany(
            """
            INSERT INTO disclosure_texts (
              asset_id, accession_number, source_url, char_count, text, status, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, accession_number) DO UPDATE SET
              source_url = excluded.source_url,
              char_count = excluded.char_count,
              text = excluded.text,
              status = excluded.status,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def accessions_with_text(self, asset_id: str) -> set[str]:
        """Accession numbers that already have usable (non-empty) text stored.

        Used by the ingest job to skip refetching. 'empty'/'failed' rows are
        excluded so a previous miss can be retried.
        """
        result = self.conn.execute(
            """
            SELECT accession_number FROM disclosure_texts
            WHERE asset_id = ? AND status = ?
            """,
            [asset_id, STATUS_FETCHED],
        ).fetchall()
        return {row[0] for row in result}

    def get(self, asset_id: str, accession_number: str) -> DisclosureText | None:
        row = self.conn.execute(
            """
            SELECT asset_id, accession_number, source_url, char_count, text, status, source
            FROM disclosure_texts
            WHERE asset_id = ? AND accession_number = ?
            """,
            [asset_id, accession_number],
        ).fetchone()
        if row is None:
            return None
        return DisclosureText(
            asset_id=row[0],
            accession_number=row[1],
            source_url=row[2],
            char_count=row[3],
            text=row[4],
            status=row[5],
            source=row[6],
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosure_texts.py::test_disclosure_text_repository_upsert_and_lookup -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/text_repository.py tests/test_disclosure_texts.py
git commit -m "✨ feat: add DisclosureTextRepository with idempotent upsert"
```

---

### Task 6: `ingest_disclosure_texts` job

**Files:**
- Create: `croesus/disclosures/text_ingest.py`
- Test: `tests/test_disclosure_texts.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosure_texts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosure_texts.py::test_ingest_disclosure_texts_fetches_skips_and_isolates -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.disclosures.text_ingest'`

- [ ] **Step 3: Implement the ingest job**

Create `croesus/disclosures/text_ingest.py`:

```python
from __future__ import annotations

from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.disclosures.repository import DisclosureRepository
from croesus.disclosures.source import DEFAULT_FORMS
from croesus.disclosures.text_extract import extract_filing_text
from croesus.disclosures.text_models import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_FETCHED,
    DisclosureText,
    DisclosureTextIngestionResult,
)
from croesus.disclosures.text_repository import DisclosureTextRepository
from croesus.disclosures.text_source import DisclosureTextSource, EdgarDocumentSource

FILER_ASSET_TYPES = ("equity",)
DEFAULT_LIMIT_PER_ASSET = 3


def ingest_disclosure_texts(
    conn: duckdb.DuckDBPyConnection,
    source: DisclosureTextSource | None = None,
    *,
    asset_ids: list[str] | None = None,
    forms: frozenset[str] | None = DEFAULT_FORMS,
    limit_per_asset: int = DEFAULT_LIMIT_PER_ASSET,
    log: Callable[[str], None] = print,
) -> DisclosureTextIngestionResult:
    """Fetch and store body text for recent filings that lack it.

    For each active equity (optionally restricted to ``asset_ids``), takes the
    most-recent ``limit_per_asset`` filings with a ``primary_doc_url`` and a
    matching form that have no stored text yet, fetches the document, extracts
    clean text, and upserts it. A failed fetch is recorded as a 'failed' row and
    isolated so one bad document never stops the run.
    """
    source = source or EdgarDocumentSource()
    wanted = set(asset_ids) if asset_ids is not None else None
    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES and (wanted is None or a.asset_id in wanted)
    ]
    disclosures = DisclosureRepository(conn)
    texts = DisclosureTextRepository(conn)
    result = DisclosureTextIngestionResult()

    for asset in assets:
        already = texts.accessions_with_text(asset.asset_id)
        candidates = [
            d
            for d in disclosures.load_for_asset(asset.asset_id)
            if d.primary_doc_url and (forms is None or d.form_type in forms)
        ]
        # Filings that already carry text are reported skipped (idempotent re-run).
        result.skipped.extend(
            d.accession_number for d in candidates if d.accession_number in already
        )
        todo = [d for d in candidates if d.accession_number not in already][:limit_per_asset]

        for disclosure in todo:
            try:
                html = source.fetch_document(disclosure.primary_doc_url)
                text = extract_filing_text(html)
                status = STATUS_FETCHED if text else STATUS_EMPTY
                texts.upsert([
                    DisclosureText(
                        asset_id=asset.asset_id,
                        accession_number=disclosure.accession_number,
                        source_url=disclosure.primary_doc_url,
                        char_count=len(text),
                        text=text,
                        status=status,
                    )
                ])
                if status == STATUS_FETCHED:
                    result.fetched.append(disclosure.accession_number)
                log(f"{asset.symbol} {disclosure.accession_number}: {status} ({len(text)} chars)")
            except Exception as exc:  # noqa: BLE001 - per-filing failures must not stop the run.
                result.failed[disclosure.accession_number] = str(exc)
                texts.upsert([
                    DisclosureText(
                        asset_id=asset.asset_id,
                        accession_number=disclosure.accession_number,
                        source_url=disclosure.primary_doc_url,
                        char_count=0,
                        text="",
                        status=STATUS_FAILED,
                    )
                ])
                log(f"failed {asset.symbol} {disclosure.accession_number}: {exc}")

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_disclosure_texts.py::test_ingest_disclosure_texts_fetches_skips_and_isolates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/disclosures/text_ingest.py tests/test_disclosure_texts.py
git commit -m "✨ feat: add filing-text ingest job with idempotent skip and isolation"
```

---

### Task 7: Wire into `local_sync` and freshness tracking

**Files:**
- Modify: `croesus/jobs/run_status.py` (add a `DomainSpec` after the `events` entry)
- Modify: `croesus/jobs/local_sync.py` (add `_run_disclosure_texts` runner; register a `SyncJob` immediately after `disclosures_run`)
- Modify: `tests/test_local_sync.py` (add `"disclosure_texts_run"` to the exact-ordered job-name list right after `"disclosures_run"`)
- Test: `tests/test_disclosure_texts.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_disclosure_texts.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_disclosure_texts.py::test_disclosure_texts_registered_in_sync_pipeline -v`
Expected: FAIL — `"disclosure_texts" not in DOMAINS_BY_NAME`.

- [ ] **Step 3: Add the freshness domain**

In `croesus/jobs/run_status.py`, inside `DOMAIN_REGISTRY`, add this entry immediately after the `events` `DomainSpec`:

```python
    # Filing-text fetches are sparse and a quiet cycle writes nothing new, so
    # MAX of any text date would read stale; like disclosures/events, key
    # freshness to the job's own last success.
    DomainSpec(
        "disclosure_texts", "disclosure_texts_run", 48.0,
        lambda c: _scalar_date(
            c,
            "SELECT MAX(finished_at) FROM job_runs "
            "WHERE job_name = 'disclosure_texts_run' AND status = 'success'",
        ),
    ),
```

- [ ] **Step 4: Add the runner**

In `croesus/jobs/local_sync.py`, add this function next to the other `_run_*` runners (e.g. after `_run_disclosures`):

```python
def _run_disclosure_texts(db: Path) -> str:
    from croesus.disclosures.text_ingest import ingest_disclosure_texts

    with get_connection(db) as conn:
        result = ingest_disclosure_texts(conn)
    return (
        f"disclosure_texts fetched={len(result.fetched)} "
        f"skip={len(result.skipped)} fail={len(result.failed)}"
    )
```

- [ ] **Step 5: Register the job**

In `croesus/jobs/local_sync.py`, in `default_sync_jobs()`, add this `SyncJob` immediately after the `disclosures_run` entry:

```python
        SyncJob(
            "disclosure_texts_run", ("disclosure_texts",), _run_disclosure_texts,
            depends_on=("disclosures_run",),
        ),
```

- [ ] **Step 6: Update the exact-order sync test**

In `tests/test_local_sync.py`, in `test_default_jobs_are_recommendation_only_no_trades`, add `"disclosure_texts_run"` to the expected ordered job-name list immediately after `"disclosures_run"`.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_disclosure_texts.py::test_disclosure_texts_registered_in_sync_pipeline tests/test_local_sync.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add croesus/jobs/run_status.py croesus/jobs/local_sync.py tests/test_disclosure_texts.py tests/test_local_sync.py
git commit -m "✨ feat: wire filing-text ingestion into local_sync pipeline"
```

---

### Task 8: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -q`
Expected: PASS — all pre-existing tests (505 after B2) plus the new disclosure-text tests are green.

- [ ] **Step 2: Confirm clean tree**

Run: `git status --short`
Expected: clean (everything committed).

---

## Self-Review (controller checklist — done while writing this plan)

**1. Spec coverage (spec §방법론 A "근거(공시에서)" + user's Option-2 decision to fetch filing body text):**
- Fetch filing body text from the stored `primary_doc_url` → Tasks 3–6. ✅
- Reuse EDGAR UA discipline → `EdgarDocumentSource` reuses `DEFAULT_USER_AGENT` (Task 4). ✅
- The text is the evidence the C2 grader will read → stored in `disclosure_texts`, keyed to the disclosure (Task 1). ✅
- No LLM, no grading here — strictly ingestion (this is C1; grading is C2). ✅
- Section-aware extraction deferred to C2 prompt assembly — documented in Scope. ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step shows complete, final code (Task 6's skip/fetch loop included). ✅

**3. Type consistency:** `DisclosureText` fields identical across Tasks 2/5/6; `DisclosureTextIngestionResult` fields (`fetched`/`skipped`/`failed`) match Task 6 assertions; `DisclosureTextSource.fetch_document(url) -> str` matches the fake and `EdgarDocumentSource`; `extract_filing_text(html, *, max_chars)` signature matches its callers; `DomainSpec("disclosure_texts","disclosure_texts_run",…)` job_name matches `SyncJob("disclosure_texts_run",…)` (Task 7); reuses real B1 symbols (`Disclosure`, `DisclosureRepository`, `DEFAULT_USER_AGENT`, `DEFAULT_FORMS`). ✅

**4. Reuse check:** uses the declared `lxml` dep (no new dependency); reuses the EDGAR UA pattern, the B1 disclosures repository/models, and the job-success freshness pattern from disclosures/events. ✅
