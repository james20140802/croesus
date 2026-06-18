# Finnhub News Ingestion Implementation Plan (News-1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest ticker-tagged company news from Finnhub's free `/company-news` API into a new `news_items` table (with an article↔asset M:N link), establishing the shared news foundation that GDELT (News-2) and the Phase C2 thesis grader will reuse.

**Architecture:** Mirror the Phase B1/C1 ingestion shape. A pure `parse_company_news(payload, symbol)` turns Finnhub's JSON into `RawNewsArticle` records (fully unit-testable). An injectable `NewsSource` protocol with a network `FinnhubNewsSource` (env-keyed) lets tests use a fake. A `NewsRepository` writes one `news_items` row per unique article plus `news_item_assets` link rows; the ingest job loops active equities with per-symbol error isolation. Wired into `local_sync`. **The article↔asset link is M:N** because one article can mention several tickers (Finnhub `related`), and GDELT articles later map to 0..N tickers via entity extraction — the schema is designed once to serve both sources.

**Tech Stack:** Python, DuckDB, `requests` (declared), stdlib `hashlib`/`datetime`. No new third-party dependencies.

---

## Scope & Boundaries

- **In scope:** Finnhub `/company-news` fetch per active equity, parse → `news_items` + `news_item_assets`, idempotent upsert, per-symbol isolation, `local_sync` wiring.
- **Out of scope (News-2):** GDELT discovery, full-article body fetching (trafilatura), entity→ticker mapping, the `news_spike` event detector. The `news_items.body` column exists (nullable) for News-2 to fill; Finnhub only provides `summary`.
- **Out of scope (C2):** any LLM grading of the news.
- **API key:** `FinnhubNewsSource` reads `CROESUS_FINNHUB_API_KEY`. Tests use a fake source (no key needed). A missing key raises a clear error only when the live source is actually used.

## Design Decisions (owned defaults)

| Decision | Choice | Rationale |
|---|---|---|
| Article identity | `item_id = sha1(f"{source}:{external_id}")` hex | Deterministic + idempotent across runs/sources; Finnhub `external_id` = article `id`, GDELT later = url. |
| Article↔asset | separate `news_item_assets` (M:N), `relation` ∈ {queried, related, entity} | One article ↔ many tickers; serves Finnhub `related` and GDELT entities without rework. |
| Asset linkage scope | only tickers present in the active-equity universe | We don't store links to assets we don't track; unknown `related` tickers are dropped. |
| Freshness | keyed to `job_runs` last success | News arrives irregularly; a quiet cycle writes nothing — same lesson as disclosures/events. |
| Lookback | `DEFAULT_LOOKBACK_DAYS = 7` per run | Finnhub `from`/`to` window; idempotent upsert dedupes overlap. |

## File Structure

| File | Responsibility |
|---|---|
| `croesus/news/__init__.py` | Package marker (empty). |
| `croesus/news/models.py` | `RawNewsArticle`, `NewsItem`, `NewsIngestionResult` + `make_item_id`. |
| `croesus/news/parse.py` | Pure `parse_company_news(payload, symbol)` → `list[RawNewsArticle]`. |
| `croesus/news/source.py` | `NewsSource` Protocol + network `FinnhubNewsSource`. |
| `croesus/news/repository.py` | `NewsRepository` (upsert items+links / load_for_asset). |
| `croesus/news/finnhub_ingest.py` | `ingest_finnhub_news(conn, source, ...)`. |
| `croesus/db/schema.sql` | Append `news_items` + `news_item_assets`. |
| `croesus/jobs/run_status.py` | Add `news_finnhub` `DomainSpec`. |
| `croesus/jobs/local_sync.py` | Add `_run_news_finnhub` runner + `SyncJob`. |
| `tests/test_news_finnhub.py` | All unit/integration tests. |

---

### Task 1: `news_items` + `news_item_assets` schema

**Files:**
- Modify: `croesus/db/schema.sql` (append at end)
- Test: `tests/test_news_finnhub.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_news_finnhub.py`:

```python
from datetime import date, datetime
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_news_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        items = {
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'news_items'"
            ).fetchall()
        }
        links = {
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'news_item_assets'"
            ).fetchall()
        }
    assert items == {
        "item_id", "source", "external_id", "url", "headline", "summary",
        "body", "published_at", "source_name", "category", "metadata", "created_at",
    }
    assert links == {"item_id", "asset_id", "relation"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_finnhub.py::test_migrate_creates_news_tables -v`
Expected: FAIL — both column sets empty (tables absent).

- [ ] **Step 3: Append the table DDL**

Append to the end of `croesus/db/schema.sql`:

```sql
-- News-1 (opportunity engine): news articles from external sources (Finnhub now;
-- GDELT in News-2). One row per unique article. ``summary`` is a short snippet;
-- ``body`` holds full text when a source provides it (GDELT/News-2; NULL for
-- Finnhub). This is raw evidence the C2 thesis grader reads — no LLM output here.
CREATE TABLE IF NOT EXISTS news_items (
  item_id       TEXT PRIMARY KEY,    -- sha1(source + ':' + external_id)
  source        TEXT NOT NULL,       -- 'finnhub' | 'gdelt'
  external_id   TEXT NOT NULL,       -- finnhub article id / gdelt url
  url           TEXT,
  headline      TEXT,
  summary       TEXT,
  body          TEXT,                -- full article text (News-2); NULL for finnhub
  published_at  TIMESTAMP,
  source_name   TEXT,                -- publisher / outlet
  category      TEXT,                -- finnhub category / gdelt theme
  metadata      JSON,
  created_at    TIMESTAMP DEFAULT now()
);

-- Article <-> asset M:N link. One article can mention several tickers (Finnhub
-- ``related``; GDELT entities later). Only assets in our universe are linked.
CREATE TABLE IF NOT EXISTS news_item_assets (
  item_id    TEXT NOT NULL,
  asset_id   TEXT NOT NULL,
  relation   TEXT NOT NULL,   -- 'queried' | 'related' | 'entity'
  PRIMARY KEY (item_id, asset_id)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_finnhub.py::test_migrate_creates_news_tables -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/db/schema.sql tests/test_news_finnhub.py
git commit -m "🗃️ chore: add news_items and news_item_assets tables"
```

---

### Task 2: models + `make_item_id`

**Files:**
- Create: `croesus/news/__init__.py`
- Create: `croesus/news/models.py`
- Test: `tests/test_news_finnhub.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_finnhub.py`:

```python
def test_news_models_and_item_id() -> None:
    from croesus.news.models import (
        NewsIngestionResult,
        RawNewsArticle,
        make_item_id,
    )

    # Deterministic + source-namespaced.
    assert make_item_id("finnhub", "12345") == make_item_id("finnhub", "12345")
    assert make_item_id("finnhub", "12345") != make_item_id("gdelt", "12345")
    assert len(make_item_id("finnhub", "12345")) == 40  # sha1 hex

    article = RawNewsArticle(
        external_id="12345",
        url="https://x.com/a",
        headline="Apple ships thing",
        summary="A summary.",
        published_at=datetime(2026, 6, 1, 12, 0, 0),
        source_name="Reuters",
        category="company news",
        tickers=("AAPL", "MSFT"),
    )
    assert article.tickers == ("AAPL", "MSFT")

    result = NewsIngestionResult()
    assert result.scanned == [] and result.stored == 0 and result.failed == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_finnhub.py::test_news_models_and_item_id -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news'`

- [ ] **Step 3: Create the package and models**

Create `croesus/news/__init__.py` (empty file).

Create `croesus/news/models.py`:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

SOURCE_FINNHUB = "finnhub"

# Article <-> asset relation kinds.
RELATION_QUERIED = "queried"   # article returned by querying this ticker
RELATION_RELATED = "related"   # listed in the source's related-tickers field
RELATION_ENTITY = "entity"     # extracted by entity recognition (News-2/GDELT)


def make_item_id(source: str, external_id: str) -> str:
    """Deterministic, source-namespaced article id (sha1 hex)."""
    return hashlib.sha1(f"{source}:{external_id}".encode("utf-8")).hexdigest()


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


@dataclass(frozen=True)
class NewsItem:
    """A persisted article row (without its asset links)."""

    item_id: str
    source: str
    external_id: str
    url: str | None
    headline: str | None
    summary: str | None
    body: str | None
    published_at: datetime | None
    source_name: str | None
    category: str | None


# Not frozen: ``stored`` is an int counter incremented in the ingest loop
# (the frozen sibling results only ever mutate containers; an int needs
# reassignment, so a plain dataclass is the honest choice here).
@dataclass
class NewsIngestionResult:
    scanned: list[str] = field(default_factory=list)      # symbols queried
    stored: int = 0                                        # article rows written
    failed: dict[str, str] = field(default_factory=dict)  # symbol -> error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_finnhub.py::test_news_models_and_item_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/news/__init__.py croesus/news/models.py tests/test_news_finnhub.py
git commit -m "✨ feat: add news models and deterministic item id"
```

---

### Task 3: pure `parse_company_news`

**Files:**
- Create: `croesus/news/parse.py`
- Test: `tests/test_news_finnhub.py` (add)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_news_finnhub.py`:

```python
def test_parse_company_news_maps_fields_and_tickers() -> None:
    from croesus.news.parse import parse_company_news

    payload = [
        {
            "id": 7777,
            "headline": "Apple unveils X",
            "summary": "Apple did a thing.",
            "url": "https://r.com/apple-x",
            "source": "Reuters",
            "datetime": 1748779200,  # 2025-06-01 12:00:00 UTC
            "related": "AAPL,MSFT",
            "category": "company",
        },
        {  # missing id -> dropped (no stable external id)
            "headline": "no id",
            "url": "https://r.com/noid",
            "datetime": 1748779200,
        },
    ]
    articles = parse_company_news(payload, symbol="AAPL")
    assert len(articles) == 1
    a = articles[0]
    assert a.external_id == "7777"
    assert a.headline == "Apple unveils X"
    assert a.source_name == "Reuters"
    assert a.published_at.year == 2025 and a.published_at.month == 6
    # Queried symbol is first; related tickers follow, de-duplicated, uppercased.
    assert a.tickers[0] == "AAPL"
    assert set(a.tickers) == {"AAPL", "MSFT"}


def test_parse_company_news_empty_and_bad_rows() -> None:
    from croesus.news.parse import parse_company_news

    assert parse_company_news([], symbol="AAPL") == []
    # A row with id 0 (falsy) is dropped; a row with no datetime keeps published_at None.
    out = parse_company_news(
        [{"id": 0, "headline": "x"}, {"id": 9, "headline": "y", "related": ""}],
        symbol="NVDA",
    )
    assert [a.external_id for a in out] == ["9"]
    assert out[0].tickers == ("NVDA",)  # empty related -> just the queried symbol
    assert out[0].published_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_news_finnhub.py -k parse_company_news -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.parse'`

- [ ] **Step 3: Implement the parser**

Create `croesus/news/parse.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from croesus.news.models import RawNewsArticle


def parse_company_news(payload: list[dict], *, symbol: str) -> list[RawNewsArticle]:
    """Parse Finnhub ``/company-news`` JSON into ``RawNewsArticle`` records.

    The queried ``symbol`` is always the first ticker; Finnhub's ``related``
    field (comma-separated) adds the rest, de-duplicated and upper-cased. Rows
    without a usable article id are dropped.
    """
    queried = symbol.upper()
    out: list[RawNewsArticle] = []
    for row in payload:
        article_id = row.get("id")
        if not article_id:  # 0 / None / missing -> no stable external id
            continue
        out.append(
            RawNewsArticle(
                external_id=str(article_id),
                url=row.get("url") or None,
                headline=row.get("headline") or None,
                summary=row.get("summary") or None,
                published_at=_parse_epoch(row.get("datetime")),
                source_name=row.get("source") or None,
                category=row.get("category") or None,
                tickers=_tickers(queried, row.get("related")),
            )
        )
    return out


def _tickers(queried: str, related: str | None) -> tuple[str, ...]:
    ordered = [queried]
    for raw in (related or "").split(","):
        ticker = raw.strip().upper()
        if ticker and ticker not in ordered:
            ordered.append(ticker)
    return tuple(ordered)


def _parse_epoch(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError, OSError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_news_finnhub.py -k parse_company_news -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/news/parse.py tests/test_news_finnhub.py
git commit -m "✨ feat: add pure Finnhub company-news parser"
```

---

### Task 4: `NewsSource` protocol + `FinnhubNewsSource`

**Files:**
- Create: `croesus/news/source.py`
- Test: `tests/test_news_finnhub.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_finnhub.py`:

```python
def test_finnhub_source_requires_key_and_satisfies_protocol(monkeypatch) -> None:
    import pytest

    from croesus.news.source import FinnhubNewsSource, NewsSource

    monkeypatch.delenv("CROESUS_FINNHUB_API_KEY", raising=False)
    with pytest.raises(ValueError):
        FinnhubNewsSource()  # no key configured

    source = FinnhubNewsSource(api_key="k")
    assert isinstance(source, NewsSource)
    assert source.name == "finnhub"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_finnhub.py::test_finnhub_source_requires_key_and_satisfies_protocol -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.source'`

- [ ] **Step 3: Implement the source**

Create `croesus/news/source.py`:

```python
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Protocol, runtime_checkable

import requests

from croesus.news.models import RawNewsArticle
from croesus.news.parse import parse_company_news

_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"


@runtime_checkable
class NewsSource(Protocol):
    name: str

    def fetch_company_news(self, symbol: str, *, since: date) -> list[RawNewsArticle]:
        """Return articles mentioning ``symbol`` published on/after ``since``."""


class FinnhubNewsSource:
    """Finnhub ``/company-news`` adapter (free tier; ticker-tagged)."""

    name = "finnhub"

    def __init__(
        self, api_key: str | None = None, *, timeout: float = 15.0
    ) -> None:
        self._api_key = api_key or os.getenv("CROESUS_FINNHUB_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Finnhub API key required: set CROESUS_FINNHUB_API_KEY or pass api_key"
            )
        self._timeout = timeout

    def fetch_company_news(self, symbol: str, *, since: date) -> list[RawNewsArticle]:
        resp = requests.get(
            _COMPANY_NEWS_URL,
            params={
                "symbol": symbol,
                "from": since.isoformat(),
                "to": (since + timedelta(days=366)).isoformat(),
                "token": self._api_key,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return parse_company_news(resp.json(), symbol=symbol)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_finnhub.py::test_finnhub_source_requires_key_and_satisfies_protocol -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/news/source.py tests/test_news_finnhub.py
git commit -m "✨ feat: add NewsSource protocol and FinnhubNewsSource"
```

---

### Task 5: `NewsRepository`

**Files:**
- Create: `croesus/news/repository.py`
- Test: `tests/test_news_finnhub.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_finnhub.py`:

```python
def test_news_repository_upsert_items_and_links(tmp_path: Path) -> None:
    from croesus.news.models import RawNewsArticle
    from croesus.news.repository import NewsRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    art = RawNewsArticle(
        external_id="7777", url="https://r.com/a", headline="H", summary="S",
        published_at=datetime(2026, 6, 1, 12, 0, 0), source_name="Reuters",
        category="company", tickers=("AAPL", "MSFT", "ZZZZ"),
    )
    with get_connection(db_path) as conn:
        repo = NewsRepository(conn)
        # Only AAPL and MSFT are in our universe; ZZZZ is dropped.
        n = repo.upsert_articles(
            "finnhub", [art], symbol_to_asset={"AAPL": "US_EQ_AAPL", "MSFT": "US_EQ_MSFT"}
        )
        assert n == 1  # one article row

        item_rows = conn.execute(
            "SELECT source, external_id, headline FROM news_items"
        ).fetchall()
        assert item_rows == [("finnhub", "7777", "H")]

        links = conn.execute(
            "SELECT asset_id, relation FROM news_item_assets ORDER BY asset_id"
        ).fetchall()
        assert links == [("US_EQ_AAPL", "queried"), ("US_EQ_MSFT", "related")]

        # Idempotent: re-upsert same article updates, no duplicate rows/links.
        repo.upsert_articles(
            "finnhub", [art], symbol_to_asset={"AAPL": "US_EQ_AAPL", "MSFT": "US_EQ_MSFT"}
        )
        assert conn.execute("SELECT count(*) FROM news_items").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM news_item_assets").fetchone()[0] == 2

        loaded = repo.load_for_asset("US_EQ_AAPL")
        assert len(loaded) == 1 and loaded[0].external_id == "7777"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_finnhub.py::test_news_repository_upsert_items_and_links -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.repository'`

- [ ] **Step 3: Implement the repository**

Create `croesus/news/repository.py`:

```python
from __future__ import annotations

import duckdb

from croesus.news.models import (
    RELATION_QUERIED,
    RELATION_RELATED,
    NewsItem,
    RawNewsArticle,
    make_item_id,
)


class NewsRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_articles(
        self,
        source: str,
        articles: list[RawNewsArticle],
        *,
        symbol_to_asset: dict[str, str],
    ) -> int:
        """Upsert articles and their asset links. ``symbol_to_asset`` maps a
        ticker symbol to an asset_id; tickers not in the map (outside our
        universe) are not linked. Returns the number of article rows submitted.
        """
        if not articles:
            return 0
        item_rows = []
        link_rows = []
        for art in articles:
            item_id = make_item_id(source, art.external_id)
            item_rows.append(
                (
                    item_id, source, art.external_id, art.url, art.headline,
                    art.summary, None, art.published_at, art.source_name, art.category,
                )
            )
            for position, symbol in enumerate(art.tickers):
                asset_id = symbol_to_asset.get(symbol)
                if asset_id is None:
                    continue
                relation = RELATION_QUERIED if position == 0 else RELATION_RELATED
                link_rows.append((item_id, asset_id, relation))

        self.conn.executemany(
            """
            INSERT INTO news_items (
              item_id, source, external_id, url, headline, summary, body,
              published_at, source_name, category
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (item_id) DO UPDATE SET
              url = excluded.url,
              headline = excluded.headline,
              summary = excluded.summary,
              published_at = excluded.published_at,
              source_name = excluded.source_name,
              category = excluded.category
            """,
            item_rows,
        )
        if link_rows:
            self.conn.executemany(
                """
                INSERT INTO news_item_assets (item_id, asset_id, relation)
                VALUES (?, ?, ?)
                ON CONFLICT (item_id, asset_id) DO UPDATE SET relation = excluded.relation
                """,
                link_rows,
            )
        return len(item_rows)

    def load_for_asset(self, asset_id: str, *, limit: int = 50) -> list[NewsItem]:
        """Most-recent-first articles linked to one asset (for C2 / the grader)."""
        rows = self.conn.execute(
            """
            SELECT i.item_id, i.source, i.external_id, i.url, i.headline, i.summary,
                   i.body, i.published_at, i.source_name, i.category
            FROM news_items i
            JOIN news_item_assets l ON l.item_id = i.item_id
            WHERE l.asset_id = ?
            ORDER BY i.published_at DESC NULLS LAST, i.item_id
            LIMIT ?
            """,
            [asset_id, limit],
        ).fetchall()
        return [
            NewsItem(
                item_id=r[0], source=r[1], external_id=r[2], url=r[3], headline=r[4],
                summary=r[5], body=r[6], published_at=r[7], source_name=r[8], category=r[9],
            )
            for r in rows
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_finnhub.py::test_news_repository_upsert_items_and_links -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/news/repository.py tests/test_news_finnhub.py
git commit -m "✨ feat: add NewsRepository with idempotent item+link upsert"
```

---

### Task 6: `ingest_finnhub_news` job

**Files:**
- Create: `croesus/news/finnhub_ingest.py`
- Test: `tests/test_news_finnhub.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_finnhub.py`:

```python
def test_ingest_finnhub_news_stores_and_isolates(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.news.finnhub_ingest import ingest_finnhub_news
    from croesus.news.models import RawNewsArticle

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    class FakeNewsSource:
        name = "finnhub"

        def fetch_company_news(self, symbol, *, since):
            if symbol == "MSFT":
                raise RuntimeError("rate limited")
            if symbol == "NVDA":
                return []
            return [RawNewsArticle(
                external_id=f"{symbol}-1", url=f"https://r.com/{symbol}",
                headline=f"{symbol} news", summary="s", published_at=None,
                source_name="Reuters", category="company", tickers=(symbol,),
            )]

    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL, MSFT, NVDA
        result = ingest_finnhub_news(conn, FakeNewsSource())
        items = conn.execute(
            "SELECT external_id FROM news_items ORDER BY external_id"
        ).fetchall()

    assert set(result.scanned) == {"AAPL", "NVDA"}   # MSFT failed, not scanned
    assert result.failed == {"MSFT": "rate limited"}
    assert result.stored == 1                         # only AAPL produced an article
    assert items == [("AAPL-1",)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_finnhub.py::test_ingest_finnhub_news_stores_and_isolates -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.news.finnhub_ingest'`

- [ ] **Step 3: Implement the ingest job**

Create `croesus/news/finnhub_ingest.py`:

```python
from __future__ import annotations

from datetime import date, timedelta
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.news.models import SOURCE_FINNHUB, NewsIngestionResult
from croesus.news.repository import NewsRepository
from croesus.news.source import FinnhubNewsSource, NewsSource

FILER_ASSET_TYPES = ("equity",)
DEFAULT_LOOKBACK_DAYS = 7


def ingest_finnhub_news(
    conn: duckdb.DuckDBPyConnection,
    source: NewsSource | None = None,
    *,
    as_of: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    log: Callable[[str], None] = print,
) -> NewsIngestionResult:
    """Fetch recent Finnhub company news for every active equity and persist it.

    Per-symbol failures are isolated so one rate-limited ticker never stops the
    run. ``as_of`` defaults to today; news from the prior ``lookback_days`` is
    requested (idempotent upsert dedupes overlap with earlier runs).
    """
    source = source or FinnhubNewsSource()
    as_of = as_of or date.today()
    since = as_of - timedelta(days=lookback_days)

    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in FILER_ASSET_TYPES
    ]
    symbol_to_asset = {a.symbol: a.asset_id for a in assets}
    repo = NewsRepository(conn)
    result = NewsIngestionResult()

    for asset in assets:
        try:
            articles = source.fetch_company_news(asset.symbol, since=since)
            stored = repo.upsert_articles(
                SOURCE_FINNHUB, articles, symbol_to_asset=symbol_to_asset
            )
            result.scanned.append(asset.symbol)
            result.stored += stored
            if stored:
                log(f"{asset.symbol}: {stored} article(s)")
        except Exception as exc:  # noqa: BLE001 - per-symbol failures must not stop the run.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_news_finnhub.py::test_ingest_finnhub_news_stores_and_isolates -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/news/finnhub_ingest.py tests/test_news_finnhub.py
git commit -m "✨ feat: add Finnhub news ingest job with per-symbol isolation"
```

---

### Task 7: Wire into `local_sync` and freshness tracking

**Files:**
- Modify: `croesus/jobs/run_status.py` (add a `DomainSpec` after the `disclosure_texts` entry)
- Modify: `croesus/jobs/local_sync.py` (add `_run_news_finnhub` runner; register a `SyncJob` after `disclosure_texts_run`)
- Modify: `tests/test_local_sync.py` (add `"news_finnhub_run"` to the ordered job-name list right after `"disclosure_texts_run"`)
- Test: `tests/test_news_finnhub.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_finnhub.py`:

```python
def test_news_finnhub_registered_in_sync_pipeline() -> None:
    from croesus.jobs.local_sync import default_sync_jobs
    from croesus.jobs.run_status import DOMAINS_BY_NAME

    assert "news_finnhub" in DOMAINS_BY_NAME
    assert DOMAINS_BY_NAME["news_finnhub"].job_name == "news_finnhub_run"

    jobs = {job.name: job for job in default_sync_jobs()}
    assert "news_finnhub_run" in jobs
    job = jobs["news_finnhub_run"]
    assert job.domains == ("news_finnhub",)
    # Independent ingestion (needs the asset universe, softly).
    assert job.soft_depends_on == ("universe_refresh",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_news_finnhub.py::test_news_finnhub_registered_in_sync_pipeline -v`
Expected: FAIL — `"news_finnhub" not in DOMAINS_BY_NAME`.

- [ ] **Step 3: Add the freshness domain**

In `croesus/jobs/run_status.py`, inside `DOMAIN_REGISTRY`, add this entry immediately after the `disclosure_texts` `DomainSpec`:

```python
    # News arrives irregularly and a quiet cycle writes nothing; like the other
    # ingestion domains, key freshness to the job's own last success.
    DomainSpec(
        "news_finnhub", "news_finnhub_run", 48.0,
        lambda c: _scalar_date(
            c,
            "SELECT MAX(finished_at) FROM job_runs "
            "WHERE job_name = 'news_finnhub_run' AND status = 'success'",
        ),
    ),
```

- [ ] **Step 4: Add the runner**

In `croesus/jobs/local_sync.py`, add this function next to the other `_run_*` runners (e.g. after `_run_disclosure_texts`):

```python
def _run_news_finnhub(db: Path) -> str:
    from croesus.news.finnhub_ingest import ingest_finnhub_news

    with get_connection(db) as conn:
        result = ingest_finnhub_news(conn)
    return (
        f"news_finnhub scanned={len(result.scanned)} "
        f"stored={result.stored} fail={len(result.failed)}"
    )
```

- [ ] **Step 5: Register the job**

In `croesus/jobs/local_sync.py`, in `default_sync_jobs()`, add this `SyncJob` immediately after the `disclosure_texts_run` entry:

```python
        SyncJob(
            "news_finnhub_run", ("news_finnhub",), _run_news_finnhub,
            soft_depends_on=("universe_refresh",),
        ),
```

- [ ] **Step 6: Update the exact-order sync test**

In `tests/test_local_sync.py`, in `test_default_jobs_are_recommendation_only_no_trades`, add `"news_finnhub_run"` to the expected ordered job-name list immediately after `"disclosure_texts_run"`.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_news_finnhub.py::test_news_finnhub_registered_in_sync_pipeline tests/test_local_sync.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add croesus/jobs/run_status.py croesus/jobs/local_sync.py tests/test_news_finnhub.py tests/test_local_sync.py
git commit -m "✨ feat: wire Finnhub news ingestion into local_sync pipeline"
```

---

### Task 8: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -q`
Expected: PASS — all pre-existing tests (517 after C1) plus the new news tests are green.

- [ ] **Step 2: Confirm clean tree**

Run: `git status --short`
Expected: clean (everything committed).

---

## Self-Review (controller checklist — done while writing this plan)

**1. Coverage of the decision (add Finnhub, shared news foundation for GDELT/C2):**
- Finnhub `/company-news` fetch + parse + persist → Tasks 3–6. ✅
- Shared `news_items` (+ `body` for News-2) + M:N `news_item_assets` → Task 1. ✅
- `NewsSource` protocol (injectable; fake in tests) + env-keyed `FinnhubNewsSource` → Task 4. ✅
- `load_for_asset` is the feed C2 will read; `news_item_assets` is what the News-2 `news_spike` detector will aggregate. ✅
- No LLM, no GDELT, no body-fetch here (News-2/C2). ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". Every code step shows complete, final code (`NewsIngestionResult` is declared non-frozen directly, with the reason in a comment). ✅

**3. Type consistency:** `RawNewsArticle`/`NewsItem` fields consistent across Tasks 2/3/5/6; `make_item_id(source, external_id)` used identically; `NewsSource.fetch_company_news(symbol, *, since) -> list[RawNewsArticle]` matches the fake, `FinnhubNewsSource`, and the ingest call; `NewsRepository.upsert_articles(source, articles, *, symbol_to_asset)` / `load_for_asset` match call sites; `DomainSpec("news_finnhub","news_finnhub_run",…)` job_name matches `SyncJob("news_finnhub_run",…)` (Task 7); reuses real `AssetRepository.list_active`. ✅

**4. Reuse check:** reuses `requests` (declared), the B1/C1 ingestion shape, the asset universe, and the job-success freshness pattern. The `news_items` schema is designed once to also serve GDELT (News-2), avoiding rework. ✅
