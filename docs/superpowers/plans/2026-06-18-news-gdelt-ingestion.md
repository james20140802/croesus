# GDELT News Ingestion Implementation Plan (News-2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest broad global news per active equity from GDELT's free, open DOC 2.0 API (policy/competitor/tech/science coverage that financial feeds miss), fetch each article's body text, and store it in the shared `news_items` tables — reusing the News-1 foundation with `source='gdelt'`.

**Architecture:** Mirror the News-1 (Finnhub) shape. Pure `company_query_term(name)` + `parse_gdelt_doc(payload)` (network-free, unit-tested). An injectable `GdeltNewsSource` (network, **no API key**) discovers article URLs+metadata by a per-company keyword query. An injectable `ArticleBodyFetcher` (`TrafilaturaBodyFetcher`, lazy-imports `trafilatura`) extracts each article's body. The ingest job links each found article to the asset it was queried for (`relation='queried'`, since we searched GDELT by that company) and stores the body. Reuses `NewsRepository` and the `news_items`/`news_item_assets` schema.

**Tech Stack:** Python, DuckDB, `requests` (declared), **`trafilatura` (NEW dependency)** for article-body extraction. The `trafilatura` import is lazy (inside the fetcher) so tests run with a fake fetcher and don't require it installed.

---

## Scope & Boundaries

- **In scope:** GDELT DOC API per-company discovery, article-body fetch, store into `news_items` (`source='gdelt'`, body populated) + `news_item_assets` link, idempotent, per-asset isolation, `local_sync` wiring.
- **Mapping approach & its limit:** we query GDELT by a cleaned company name and link the returned articles to that asset. This is deterministic and reuses the schema, but **company-name ambiguity** (e.g. "Apple" the fruit, "Target" the retailer) lets in some false matches — accepted for a first cut; the Phase C2 LLM provides the final relevance judgment, and a name query already catches far broader coverage than Finnhub's financial-only feed.
- **Deferred (follow-ups):** theme-based catalyst discovery (articles not yet tied to any ticker) + NER/GKG entity→ticker mapping; the GDELT `timelinevol` **`news_spike` event detector** (revives the B2 deferred trigger). These are separate sub-phases.
- **Out of scope (C2):** any LLM grading/relevance scoring of the news.
- **No API key** (GDELT is open). **External fetching:** the body fetcher requests arbitrary publisher URLs (GDELT's open license covers its metadata; the underlying article text is third-party — acceptable for personal, non-distributed research, consistent with the C1 filing-text decision).

## File Structure

| File | Responsibility |
|---|---|
| `croesus/news/models.py` | (modify) add `body: str \| None = None` to `RawNewsArticle`. |
| `croesus/news/repository.py` | (modify) write `art.body` instead of a hardcoded `None`. |
| `croesus/news/gdelt_parse.py` | Pure `company_query_term(name)` + `parse_gdelt_doc(payload)`. |
| `croesus/news/gdelt_source.py` | `GdeltNewsSource` Protocol + network `GdeltDocSource`. |
| `croesus/news/body_fetch.py` | `ArticleBodyFetcher` Protocol + `TrafilaturaBodyFetcher` (lazy import). |
| `croesus/news/gdelt_ingest.py` | `ingest_gdelt_news(conn, source, body_fetcher, ...)`. |
| `pyproject.toml` | (modify) add `trafilatura` to dependencies. |
| `croesus/jobs/run_status.py` | Add `news_gdelt` `DomainSpec` (via `_job_success_date_fn`). |
| `croesus/jobs/local_sync.py` | Add `_run_news_gdelt` runner + `SyncJob`. |
| `tests/test_news_gdelt.py` | All unit/integration tests. |

---

### Task 1: `RawNewsArticle.body` + repository writes it

**Files:**
- Modify: `croesus/news/models.py`
- Modify: `croesus/news/repository.py`
- Test: `tests/test_news_gdelt.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_news_gdelt.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_gdelt.py::test_repository_persists_article_body -v`
Expected: FAIL — `RawNewsArticle.__init__() got an unexpected keyword argument 'body'`.

- [ ] **Step 3: Add the `body` field**

In `croesus/news/models.py`, add a `body` field to `RawNewsArticle` (after `tickers`):

```python
@dataclass(frozen=True)
class RawNewsArticle:
    """A news article as parsed from a source, with the tickers it relates to."""

    external_id: str
    url: str | None
    headline: str | None
    summary: str | None
    published_at: datetime | None
    source_name: str | None
    category: str | None
    tickers: tuple[str, ...]   # symbols the source associates (1st = queried)
    body: str | None = None    # full article text (GDELT); None for headline-only sources
```

- [ ] **Step 4: Write the body in the repository**

In `croesus/news/repository.py`, in `upsert_articles`, change the `item_rows.append(...)` tuple's 7th element from the hardcoded `None` to `art.body`:

```python
            item_rows.append(
                (
                    item_id, source, art.external_id, art.url, art.headline,
                    art.summary, art.body, art.published_at, art.source_name, art.category,
                )
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_news_gdelt.py::test_repository_persists_article_body -v`
Expected: PASS

- [ ] **Step 6: Run the News-1 suite to confirm no regression**

Run: `pytest tests/test_news_finnhub.py -v`
Expected: PASS — Finnhub articles construct `RawNewsArticle` without `body` (defaults to `None`), and the repo now writes that `None` (same as before).

- [ ] **Step 7: Commit**

```bash
git add croesus/news/models.py croesus/news/repository.py tests/test_news_gdelt.py
git commit -m "✨ feat: add body field to RawNewsArticle and persist it"
```

---

### Task 2: pure `company_query_term` + `parse_gdelt_doc`

**Files:**
- Create: `croesus/news/gdelt_parse.py`
- Test: `tests/test_news_gdelt.py` (add)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_news_gdelt.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_gdelt.py -k "company_query_term or parse_gdelt_doc" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.gdelt_parse'`

- [ ] **Step 3: Implement the parsers**

Create `croesus/news/gdelt_parse.py`:

```python
from __future__ import annotations

import re
from datetime import datetime, timezone

from croesus.news.models import RawNewsArticle

# Common corporate-name suffixes to strip so the keyword query matches plain news
# prose ("Apple" not "Apple Inc."). Order-independent; applied as whole words.
_SUFFIXES = (
    "incorporated", "inc", "corporation", "corp", "company", "co",
    "limited", "ltd", "plc", "holdings", "group", "class a", "class b",
)
_SUFFIX_RE = re.compile(
    r"[,\.]?\s*\b(" + "|".join(_SUFFIXES) + r")\b\.?\s*$", re.IGNORECASE
)


def company_query_term(name: str | None) -> str:
    """Clean a company name into a quoted GDELT keyword phrase.

    Strips trailing corporate suffixes ("Inc.", "Corporation", "Class A", …) and
    wraps the result in quotes for an exact-phrase match. Returns "" when there
    is no usable name (caller skips that asset).
    """
    if not name:
        return ""
    cleaned = name.strip()
    # Strip suffixes repeatedly (e.g. "Alphabet Inc. Class A" -> "Alphabet").
    while True:
        stripped = _SUFFIX_RE.sub("", cleaned).strip(" ,.")
        if stripped == cleaned or not stripped:
            break
        cleaned = stripped
    return f'"{cleaned}"' if cleaned else ""


def parse_gdelt_doc(payload: dict) -> list[RawNewsArticle]:
    """Parse a GDELT DOC 2.0 ``artlist`` JSON response into ``RawNewsArticle``.

    Tickers are left empty (the ingest job attaches the queried asset) and body
    is None (fetched separately). Rows without a URL are dropped.
    """
    articles = payload.get("articles") if isinstance(payload, dict) else None
    if not isinstance(articles, list):
        return []
    out: list[RawNewsArticle] = []
    for row in articles:
        url = row.get("url") or None
        if not url:
            continue
        out.append(
            RawNewsArticle(
                external_id=url,
                url=url,
                headline=row.get("title") or None,
                summary=None,
                published_at=_parse_seendate(row.get("seendate")),
                source_name=row.get("domain") or None,
                category=None,
                tickers=(),
            )
        )
    return out


def _parse_seendate(value: object) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).replace(tzinfo=None)
    except ValueError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_news_gdelt.py -k "company_query_term or parse_gdelt_doc" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/news/gdelt_parse.py tests/test_news_gdelt.py
git commit -m "✨ feat: add pure GDELT query-term and DOC parser"
```

---

### Task 3: `GdeltNewsSource` protocol + `GdeltDocSource`

**Files:**
- Create: `croesus/news/gdelt_source.py`
- Test: `tests/test_news_gdelt.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_gdelt.py`:

```python
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
    assert params["enddatetime"] == "20260608000000"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_gdelt.py::test_gdelt_source_satisfies_protocol_and_builds_params -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.gdelt_source'`

- [ ] **Step 3: Implement the source**

Create `croesus/news/gdelt_source.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable

import requests

from croesus.news.gdelt_parse import parse_gdelt_doc
from croesus.news.models import RawNewsArticle

_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
DEFAULT_MAX_RECORDS = 25


@runtime_checkable
class GdeltNewsSource(Protocol):
    name: str

    def fetch_articles(
        self, query_term: str, *, since: date, until: date
    ) -> list[RawNewsArticle]:
        """Return articles matching ``query_term`` in ``[since, until]``."""


class GdeltDocSource:
    """GDELT DOC 2.0 API adapter (free, open, no key)."""

    name = "gdelt"

    def __init__(
        self, *, max_records: int = DEFAULT_MAX_RECORDS, timeout: float = 20.0
    ) -> None:
        self._max_records = max_records
        self._timeout = timeout

    def build_params(self, query_term: str, *, since: date, until: date) -> dict:
        return {
            "query": f"{query_term} sourcelang:english",
            "mode": "artlist",
            "format": "json",
            "maxrecords": self._max_records,
            "sort": "DateDesc",
            "startdatetime": since.strftime("%Y%m%d000000"),
            "enddatetime": until.strftime("%Y%m%d000000"),
        }

    def fetch_articles(
        self, query_term: str, *, since: date, until: date
    ) -> list[RawNewsArticle]:
        resp = requests.get(
            _DOC_URL,
            params=self.build_params(query_term, since=since, until=until),
            timeout=self._timeout,
        )
        resp.raise_for_status()
        # GDELT returns an empty body (not JSON) when a query matches nothing.
        if not resp.text.strip():
            return []
        return parse_gdelt_doc(resp.json())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_gdelt.py::test_gdelt_source_satisfies_protocol_and_builds_params -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/news/gdelt_source.py tests/test_news_gdelt.py
git commit -m "✨ feat: add GdeltNewsSource protocol and GdeltDocSource"
```

---

### Task 4: `ArticleBodyFetcher` + `TrafilaturaBodyFetcher` + dependency

**Files:**
- Create: `croesus/news/body_fetch.py`
- Modify: `pyproject.toml`
- Test: `tests/test_news_gdelt.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_gdelt.py`:

```python
def test_article_body_fetcher_protocol() -> None:
    from croesus.news.body_fetch import ArticleBodyFetcher, TrafilaturaBodyFetcher

    fetcher = TrafilaturaBodyFetcher()
    assert isinstance(fetcher, ArticleBodyFetcher)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_gdelt.py::test_article_body_fetcher_protocol -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.body_fetch'`

- [ ] **Step 3: Implement the fetcher**

Create `croesus/news/body_fetch.py`:

```python
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ArticleBodyFetcher(Protocol):
    def fetch_body(self, url: str) -> str | None:
        """Return the cleaned article body text at ``url``, or None if unavailable."""


class TrafilaturaBodyFetcher:
    """Fetches and extracts an article's main text with ``trafilatura``.

    ``trafilatura`` is imported lazily so tests (which inject a fake fetcher)
    don't require it installed, and an extraction failure yields ``None`` rather
    than raising — a missing body must never stop a news ingest run.
    """

    def __init__(self, *, timeout: float = 20.0) -> None:
        self._timeout = timeout

    def fetch_body(self, url: str) -> str | None:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None
        text = trafilatura.extract(downloaded)
        return text or None
```

- [ ] **Step 4: Add the dependency**

In `pyproject.toml`, add `trafilatura` to the runtime dependencies list (next to `lxml`):

```toml
  "trafilatura>=1.6",
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_news_gdelt.py::test_article_body_fetcher_protocol -v`
Expected: PASS (the `isinstance` Protocol check does not import `trafilatura`; the lazy import only runs inside `fetch_body`).

- [ ] **Step 6: Commit**

```bash
git add croesus/news/body_fetch.py pyproject.toml tests/test_news_gdelt.py
git commit -m "✨ feat: add ArticleBodyFetcher with lazy trafilatura backend"
```

---

### Task 5: `ingest_gdelt_news` job

**Files:**
- Create: `croesus/news/gdelt_ingest.py`
- Test: `tests/test_news_gdelt.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_gdelt.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_gdelt.py::test_ingest_gdelt_news_links_bodies_and_isolates -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.gdelt_ingest'`

- [ ] **Step 3: Implement the ingest job**

Create `croesus/news/gdelt_ingest.py`:

```python
from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.news.body_fetch import ArticleBodyFetcher, TrafilaturaBodyFetcher
from croesus.news.gdelt_parse import company_query_term
from croesus.news.gdelt_source import GdeltDocSource, GdeltNewsSource
from croesus.news.models import NewsIngestionResult
from croesus.news.repository import NewsRepository

SOURCE_GDELT = "gdelt"
FILER_ASSET_TYPES = ("equity",)
DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_LIMIT_PER_ASSET = 5


def ingest_gdelt_news(
    conn: duckdb.DuckDBPyConnection,
    source: GdeltNewsSource | None = None,
    body_fetcher: ArticleBodyFetcher | None = None,
    *,
    as_of: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    limit_per_asset: int = DEFAULT_LIMIT_PER_ASSET,
    log: Callable[[str], None] = print,
) -> NewsIngestionResult:
    """Discover broad GDELT news per active equity (by company name), fetch each
    article's body, and persist it (``source='gdelt'``, ``relation='queried'``).

    Per-asset failures are isolated. Assets with no usable name are skipped.
    """
    source = source or GdeltDocSource()
    body_fetcher = body_fetcher or TrafilaturaBodyFetcher()
    as_of = as_of or date.today()
    since = as_of - timedelta(days=lookback_days)

    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES
    ]
    repo = NewsRepository(conn)
    result = NewsIngestionResult()

    for asset in assets:
        query_term = company_query_term(asset.name)
        if not query_term:
            continue  # no usable company name to query GDELT with
        try:
            articles = source.fetch_articles(query_term, since=since, until=as_of)
            enriched = [
                replace(
                    art,
                    tickers=(asset.symbol,),
                    body=(body_fetcher.fetch_body(art.url) if art.url else None),
                )
                for art in articles[:limit_per_asset]
            ]
            stored = repo.upsert_articles(
                SOURCE_GDELT,
                enriched,
                symbol_to_asset={asset.symbol.upper(): asset.asset_id},
            )
            result.scanned.append(asset.symbol)
            result.stored += stored
            if stored:
                log(f"{asset.symbol}: {stored} GDELT article(s)")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_gdelt.py::test_ingest_gdelt_news_links_bodies_and_isolates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/news/gdelt_ingest.py tests/test_news_gdelt.py
git commit -m "✨ feat: add GDELT news ingest job with per-asset body fetch"
```

---

### Task 6: Wire into `local_sync` and freshness tracking

**Files:**
- Modify: `croesus/jobs/run_status.py` (add a `DomainSpec` after `news_finnhub`)
- Modify: `croesus/jobs/local_sync.py` (add `_run_news_gdelt` runner; register a `SyncJob` after `news_finnhub_run`)
- Modify: `tests/test_local_sync.py` (add `"news_gdelt_run"` to the ordered job-name list right after `"news_finnhub_run"`)
- Test: `tests/test_news_gdelt.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_gdelt.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_gdelt.py::test_news_gdelt_registered_in_sync_pipeline -v`
Expected: FAIL — `"news_gdelt" not in DOMAINS_BY_NAME`.

- [ ] **Step 3: Add the freshness domain**

In `croesus/jobs/run_status.py`, inside `DOMAIN_REGISTRY`, add this entry immediately after the `news_finnhub` `DomainSpec`:

```python
    # GDELT broad news; like the other ingestion domains, key freshness to the
    # job's own last success (a quiet cycle writes nothing).
    DomainSpec(
        "news_gdelt", "news_gdelt_run", 48.0,
        _job_success_date_fn("news_gdelt_run"),
    ),
```

- [ ] **Step 4: Add the runner**

In `croesus/jobs/local_sync.py`, add this function next to the other `_run_*` runners (e.g. after `_run_news_finnhub`):

```python
def _run_news_gdelt(db: Path) -> str:
    from croesus.news.gdelt_ingest import ingest_gdelt_news

    with get_connection(db) as conn:
        result = ingest_gdelt_news(conn)
    return (
        f"news_gdelt scanned={len(result.scanned)} "
        f"stored={result.stored} fail={len(result.failed)}"
    )
```

- [ ] **Step 5: Register the job**

In `croesus/jobs/local_sync.py`, in `default_sync_jobs()`, add this `SyncJob` immediately after the `news_finnhub_run` entry:

```python
        SyncJob(
            "news_gdelt_run", ("news_gdelt",), _run_news_gdelt,
            soft_depends_on=("universe_refresh",),
        ),
```

- [ ] **Step 6: Update the exact-order sync test**

In `tests/test_local_sync.py`, in `test_default_jobs_are_recommendation_only_no_trades`, add `"news_gdelt_run"` to the expected ordered job-name list immediately after `"news_finnhub_run"`.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_news_gdelt.py::test_news_gdelt_registered_in_sync_pipeline tests/test_local_sync.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add croesus/jobs/run_status.py croesus/jobs/local_sync.py tests/test_news_gdelt.py tests/test_local_sync.py
git commit -m "✨ feat: wire GDELT news ingestion into local_sync pipeline"
```

---

### Task 7: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -q`
Expected: PASS — all pre-existing tests (527 after News-1) plus the new GDELT tests are green.

- [ ] **Step 2: Confirm clean tree**

Run: `git status --short`
Expected: clean (everything committed).

---

## Self-Review (controller checklist — done while writing this plan)

**1. Coverage of the decision (add GDELT broad news, reuse the news foundation):**
- GDELT DOC API per-company discovery → Tasks 2–3. ✅
- Article body fetch (trafilatura, injectable) → Task 4. ✅
- Reuse `news_items`/`news_item_assets` with `source='gdelt'`, body populated → Tasks 1, 5. ✅
- Per-asset isolation; assets with no name skipped → Task 5. ✅
- No LLM here; theme-discovery + NER mapping + `news_spike` detector explicitly deferred. ✅ (documented in Scope)

**2. Placeholder scan:** No TBD/TODO. Every code step shows complete code. The mapping-ambiguity limitation is documented as an accepted first-cut decision, not an unfilled gap. ✅

**3. Type consistency:** `RawNewsArticle` gains `body` (default None) — News-1's `parse_company_news` still constructs it without `body` (Task 1 Step 6 verifies). `GdeltNewsSource.fetch_articles(query_term, *, since, until) -> list[RawNewsArticle]` matches the fake and `GdeltDocSource`; `ArticleBodyFetcher.fetch_body(url) -> str|None` matches the fake and `TrafilaturaBodyFetcher`; `ingest_gdelt_news` reuses `NewsRepository.upsert_articles(source, articles, *, symbol_to_asset)` and `NewsIngestionResult`; `DomainSpec("news_gdelt","news_gdelt_run",…)` job_name matches `SyncJob("news_gdelt_run",…)` and uses the `_job_success_date_fn` factory from News-1's code-review. ✅

**4. Reuse check:** reuses the News-1 `NewsRepository`/schema/result type and the `_job_success_date_fn` factory; the only new dependency is `trafilatura` (lazy-imported, fake in tests). Mirrors the Finnhub ingest shape. ✅
