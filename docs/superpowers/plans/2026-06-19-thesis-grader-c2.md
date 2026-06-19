# Phase C2: LLM Structural-Thesis Grader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an LLM grader that reads SEC filing text + news + numeric context for event-prefiltered candidate equities and emits discrete, evidence-backed structural-thesis grades (moat / tech / sector-trajectory / disruption-risk + bear case + confidence), persisted to a new `thesis_grades` table.

**Architecture:** A new `croesus/research/thesis_*` subsystem mirroring the existing `croesus/research/agent.py` pattern: an evidence assembler reads three repositories, a prompt builder renders a strict-JSON rubric, an OpenAI-compatible local LLM (`ChatClient`) grades, a tolerant parser validates grades against fixed allowed-value sets, and a repository idempotently upserts. The grader funnels on the B2 events table (LLM only on the shortlist) and is wired into `local_sync` after `event_scan`. C2 STOPS at persisting grades — wiring grades into DCF knobs and intrinsic-value bands is Phase C3 (separate plan); this plan does NOT touch `compute_valuation.py` or `valuation.py`.

**Tech Stack:** Python, DuckDB, the existing `croesus/research/llm_client.py` (`ChatClient` Protocol, `ChatCompletionsClient`, `LlmError`/`LlmUnavailable`), pytest with a fake chat client.

---

## File Structure

- Create: `croesus/research/thesis_models.py` — grade taxonomies, status constants, `ThesisGrade`, `ThesisRunResult`.
- Create: `croesus/research/thesis_evidence.py` — `ThesisEvidence` + `assemble_thesis_evidence(conn, asset, as_of)`.
- Create: `croesus/research/thesis_prompt.py` — `build_thesis_messages(asset, evidence)`.
- Create: `croesus/research/thesis_parse.py` — `parse_thesis_payload(raw)`.
- Create: `croesus/research/thesis_repository.py` — `ThesisGradeRepository`.
- Create: `croesus/research/thesis_grader.py` — `grade_theses(conn, ...)`.
- Modify: `croesus/db/schema.sql` — add `thesis_grades` table.
- Modify: `croesus/jobs/run_status.py` — add `thesis_grades` `DomainSpec`.
- Modify: `croesus/jobs/local_sync.py` — add `_run_thesis_grader` + `SyncJob`.
- Test: `tests/test_thesis_grader.py` — all unit + integration tests.

**Decisions locked in (do not deviate):**
- **Funnel:** grade only assets with a row in `events` for `as_of_date` (`SELECT DISTINCT asset_id FROM events WHERE as_of_date = ?`). No events → nothing to grade.
- **Taxonomy:** four graded dimensions (moat, tech, sector, disruption) per spec §방법론 A. Only three feed C3's `DcfKnobs` (moat→CAP, sector→terminal, disruption→risk-premium); `tech` is human-review evidence with no knob.
- **Evidence guardrail:** every dimension carries an evidence string; one overall `evidence_source` ∈ {`filing`, `general_knowledge`} distinguishes filing-defensible from general-knowledge; a `bear_case` is always required.
- **Persistence:** natural-key upsert on `(asset_id, as_of_date)` (the disclosures/events/news house pattern), NOT the append-only `research_notes` pattern.
- **Failure contract** (mirror `agent.py`): `LlmUnavailable` → set `skipped_reason`, stop the whole run (server down); `LlmError` or parse `ValueError` → persist one `failed` grade, continue to the next asset. The pipeline is never blocked by the LLM.

---

### Task 1: Thesis models — taxonomies, status, dataclasses

**Files:**
- Create: `croesus/research/thesis_models.py`
- Test: `tests/test_thesis_grader.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_thesis_grader.py
from datetime import date


def test_thesis_models_taxonomies_and_defaults() -> None:
    from croesus.research.thesis_models import (
        CONFIDENCE_LEVELS,
        DISRUPTION_GRADES,
        EVIDENCE_SOURCES,
        MOAT_GRADES,
        SECTOR_GRADES,
        STATUS_FAILED,
        STATUS_GENERATED,
        TECH_GRADES,
        ThesisGrade,
        ThesisRunResult,
    )

    assert MOAT_GRADES == ("wide", "narrow", "none")
    assert TECH_GRADES == ("leading", "parity", "lagging")
    assert SECTOR_GRADES == ("secular_growth", "stable", "declining")
    assert DISRUPTION_GRADES == ("low", "medium", "high")
    assert CONFIDENCE_LEVELS == ("high", "medium", "low")
    assert EVIDENCE_SOURCES == ("filing", "general_knowledge")
    assert STATUS_GENERATED == "generated" and STATUS_FAILED == "failed"

    grade = ThesisGrade(
        asset_id="US_EQ_AAPL", as_of_date=date(2026, 6, 19),
        run_id="r1", model="qwen3:32b", status=STATUS_GENERATED,
    )
    assert grade.moat_grade is None and grade.metadata == {}

    result = ThesisRunResult(run_id="r1")
    assert result.grades == [] and result.generated == 0 and result.failed == 0
    assert result.skipped_reason is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_thesis_grader.py::test_thesis_models_taxonomies_and_defaults -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.research.thesis_models'`

- [ ] **Step 3: Write minimal implementation**

```python
# croesus/research/thesis_models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

STATUS_GENERATED = "generated"
STATUS_FAILED = "failed"

# Discrete grade vocabularies (spec §방법론 A). The first three map to C3's
# DcfKnobs (moat→CAP years, sector→terminal growth, disruption→WACC premium);
# tech is human-review evidence with no knob.
MOAT_GRADES = ("wide", "narrow", "none")
TECH_GRADES = ("leading", "parity", "lagging")
SECTOR_GRADES = ("secular_growth", "stable", "declining")
DISRUPTION_GRADES = ("low", "medium", "high")
CONFIDENCE_LEVELS = ("high", "medium", "low")
# Whether the thesis is defensible from the filing or rests on general knowledge.
EVIDENCE_SOURCES = ("filing", "general_knowledge")


@dataclass(frozen=True)
class ThesisGrade:
    """One asset's structural-thesis grade on a given date.

    A ``failed`` grade carries ``error`` and leaves the grade fields None; a
    ``generated`` grade carries all four dimension grades, their evidence, a
    bear case, a confidence, and an evidence source.
    """

    asset_id: str
    as_of_date: date
    run_id: str
    model: str
    status: str
    moat_grade: str | None = None
    moat_evidence: str | None = None
    tech_grade: str | None = None
    tech_evidence: str | None = None
    sector_grade: str | None = None
    sector_evidence: str | None = None
    disruption_grade: str | None = None
    disruption_evidence: str | None = None
    bear_case: str | None = None
    confidence: str | None = None
    evidence_source: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Not frozen: int counters are reassigned in the grader loop (mirrors
# NewsIngestionResult / ResearchRunResult).
@dataclass
class ThesisRunResult:
    run_id: str
    grades: list[ThesisGrade] = field(default_factory=list)
    generated: int = 0
    failed: int = 0
    skipped_reason: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thesis_grader.py::test_thesis_models_taxonomies_and_defaults -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/research/thesis_models.py tests/test_thesis_grader.py
git commit -m "✨ feat: add thesis-grade taxonomies and models (C2)"
```

---

### Task 2: Tolerant thesis-payload parser

**Files:**
- Create: `croesus/research/thesis_parse.py`
- Test: `tests/test_thesis_grader.py`

**Context:** Reuse `agent.py`'s exact tolerance strategy — strip `<think>…</think>` (qwen3 reasoning traces) with DOTALL, then take the substring from the first `{` to the last `}` and `json.loads` it. Then validate every grade against its allowed-value tuple, require non-empty evidence strings, a bear case, a confidence, and an evidence source. Any violation raises `ValueError` so the grader records a `failed` grade and continues.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_thesis_grader.py
import pytest

_VALID_PAYLOAD = """
<think>let me reason about the moat...</think>
Here is my assessment:
```json
{
  "moat_grade": "wide", "moat_evidence": "Switching costs cited in 10-K Item 1.",
  "tech_grade": "leading", "tech_evidence": "R&D 8% of revenue, roadmap in MD&A.",
  "sector_grade": "secular_growth", "sector_evidence": "TAM expanding per filing.",
  "disruption_grade": "low", "disruption_evidence": "No new entrants noted.",
  "bear_case": "A platform shift could erode switching costs.",
  "confidence": "high", "evidence_source": "filing"
}
```
"""


def test_parse_thesis_payload_extracts_and_validates() -> None:
    from croesus.research.thesis_parse import parse_thesis_payload

    data = parse_thesis_payload(_VALID_PAYLOAD)
    assert data["moat_grade"] == "wide"
    assert data["sector_grade"] == "secular_growth"
    assert data["disruption_grade"] == "low"
    assert data["confidence"] == "high"
    assert data["evidence_source"] == "filing"
    assert data["bear_case"].startswith("A platform shift")


def test_parse_thesis_payload_rejects_bad_grade_value() -> None:
    from croesus.research.thesis_parse import parse_thesis_payload

    bad = _VALID_PAYLOAD.replace('"moat_grade": "wide"', '"moat_grade": "huge"')
    with pytest.raises(ValueError):
        parse_thesis_payload(bad)


def test_parse_thesis_payload_rejects_missing_evidence() -> None:
    from croesus.research.thesis_parse import parse_thesis_payload

    bad = _VALID_PAYLOAD.replace(
        '"moat_evidence": "Switching costs cited in 10-K Item 1.",',
        '"moat_evidence": "   ",',
    )
    with pytest.raises(ValueError):
        parse_thesis_payload(bad)


def test_parse_thesis_payload_rejects_no_json() -> None:
    from croesus.research.thesis_parse import parse_thesis_payload

    with pytest.raises(ValueError):
        parse_thesis_payload("<think>only reasoning, no object</think>")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_thesis_grader.py -k parse_thesis -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# croesus/research/thesis_parse.py
from __future__ import annotations

import json
import re

from croesus.research.thesis_models import (
    CONFIDENCE_LEVELS,
    DISRUPTION_GRADES,
    EVIDENCE_SOURCES,
    MOAT_GRADES,
    SECTOR_GRADES,
    TECH_GRADES,
)

# qwen3-style reasoning traces wrap deliberation in <think> tags; the grades are
# whatever JSON follows.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# (grade key, evidence key, allowed values).
_DIMENSIONS = (
    ("moat_grade", "moat_evidence", MOAT_GRADES),
    ("tech_grade", "tech_evidence", TECH_GRADES),
    ("sector_grade", "sector_evidence", SECTOR_GRADES),
    ("disruption_grade", "disruption_evidence", DISRUPTION_GRADES),
)


def _require_str(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"missing or empty {key!r}")
    return value.strip()


def parse_thesis_payload(raw: str) -> dict[str, str]:
    """Strip reasoning, extract the JSON object, and validate it.

    Tolerates markdown fences and prose around the object (first ``{`` to last
    ``}``). Raises ValueError on any missing field, empty evidence, or
    out-of-vocabulary grade so the grader can record a ``failed`` grade.
    """
    text = _THINK_RE.sub("", raw)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in model response")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model response JSON is not an object")

    payload: dict[str, str] = {}
    for grade_key, evidence_key, allowed in _DIMENSIONS:
        grade = _require_str(data, grade_key)
        if grade not in allowed:
            raise ValueError(f"{grade_key}={grade!r} not in {allowed}")
        payload[grade_key] = grade
        payload[evidence_key] = _require_str(data, evidence_key)

    payload["bear_case"] = _require_str(data, "bear_case")

    confidence = _require_str(data, "confidence")
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence={confidence!r} not in {CONFIDENCE_LEVELS}")
    payload["confidence"] = confidence

    source = _require_str(data, "evidence_source")
    if source not in EVIDENCE_SOURCES:
        raise ValueError(f"evidence_source={source!r} not in {EVIDENCE_SOURCES}")
    payload["evidence_source"] = source

    return payload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thesis_grader.py -k parse_thesis -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/research/thesis_parse.py tests/test_thesis_grader.py
git commit -m "✨ feat: add tolerant thesis-payload parser with grade validation (C2)"
```

---

### Task 3: Evidence assembler

**Files:**
- Create: `croesus/research/thesis_evidence.py`
- Test: `tests/test_thesis_grader.py`

**Context:** Reads three repositories for one asset and bundles the evidence the prompt will render. The filing excerpt is the most-recent FETCHED filing text (joined `disclosures`→`disclosure_texts`), truncated to a char budget so a 10-K doesn't blow the context window. News is the top-N most recent linked items. Numeric context is the latest valuation snapshot plus a few key fundamentals. Everything is best-effort: a missing source yields None / empty, never an error.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_thesis_grader.py
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_assemble_thesis_evidence_reads_filing_news_numbers(tmp_path: Path) -> None:
    from croesus.assets.models import Asset
    from croesus.assets.repository import AssetRepository
    from croesus.news.models import RawNewsArticle
    from croesus.news.repository import NewsRepository
    from croesus.research.thesis_evidence import assemble_thesis_evidence

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 19)
    with get_connection(db_path) as conn:
        AssetRepository(conn).upsert_many([Asset(
            asset_id="US_EQ_AAPL", symbol="AAPL", name="Apple Inc.",
            asset_type="equity", sector="Tech", industry="Hardware",
        )])
        # A fetched filing + its text.
        conn.execute(
            "INSERT INTO disclosures (asset_id, accession_number, form_type, "
            "filed_date, source) VALUES (?, ?, ?, ?, ?)",
            ["US_EQ_AAPL", "acc-1", "10-K", date(2026, 5, 1), "sec_edgar"],
        )
        conn.execute(
            "INSERT INTO disclosure_texts (asset_id, accession_number, char_count, "
            "text, status, source) VALUES (?, ?, ?, ?, ?, ?)",
            ["US_EQ_AAPL", "acc-1", 5, "RISK FACTORS body" * 5000, "fetched", "sec_edgar"],
        )
        NewsRepository(conn).upsert_articles("gdelt", [RawNewsArticle(
            external_id="u1", url="u1", headline="Apple launches X", summary=None,
            published_at=None, source_name="reuters.com", category=None,
            tickers=("AAPL",), body="full body",
        )], symbol_to_asset={"AAPL": "US_EQ_AAPL"})

        asset = AssetRepository(conn).list_active()[0]
        ev = assemble_thesis_evidence(conn, asset, asof, filing_char_budget=100)

    assert ev.filing_form == "10-K"
    assert ev.filing_excerpt is not None and len(ev.filing_excerpt) <= 100
    assert any(n.headline == "Apple launches X" for n in ev.news)
    assert "revenue" in ev.fundamentals  # key present even if value is None


def test_assemble_thesis_evidence_tolerates_missing_sources(tmp_path: Path) -> None:
    from croesus.assets.models import Asset
    from croesus.assets.repository import AssetRepository
    from croesus.research.thesis_evidence import assemble_thesis_evidence

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        AssetRepository(conn).upsert_many([Asset(
            asset_id="US_EQ_ZZZ", symbol="ZZZ", name="Zed Co.", asset_type="equity",
        )])
        asset = AssetRepository(conn).list_active()[0]
        ev = assemble_thesis_evidence(conn, asset, date(2026, 6, 19))

    assert ev.filing_excerpt is None and ev.filing_form is None
    assert ev.news == [] and ev.valuation is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_thesis_grader.py -k assemble_thesis -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# croesus/research/thesis_evidence.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

from croesus.assets.models import Asset
from croesus.factors.equity.repository import (
    ValuationSnapshot,
    ValuationSnapshotRepository,
)
from croesus.fundamentals.repository import (
    METRIC_FREE_CASH_FLOW,
    METRIC_NET_INCOME,
    METRIC_REVENUE,
    FundamentalsRepository,
)
from croesus.news.models import NewsItem
from croesus.news.repository import NewsRepository

DEFAULT_FILING_CHAR_BUDGET = 24_000
DEFAULT_NEWS_LIMIT = 10

# Key fundamentals surfaced to the grader as numeric context.
_FUNDAMENTAL_METRICS = {
    "revenue": METRIC_REVENUE,
    "free_cash_flow": METRIC_FREE_CASH_FLOW,
    "net_income": METRIC_NET_INCOME,
}


@dataclass(frozen=True)
class ThesisEvidence:
    filing_excerpt: str | None
    filing_form: str | None
    filing_date: date | None
    news: list[NewsItem]
    valuation: ValuationSnapshot | None
    fundamentals: dict[str, float | None]


def assemble_thesis_evidence(
    conn: duckdb.DuckDBPyConnection,
    asset: Asset,
    as_of: date,
    *,
    filing_char_budget: int = DEFAULT_FILING_CHAR_BUDGET,
    news_limit: int = DEFAULT_NEWS_LIMIT,
) -> ThesisEvidence:
    """Bundle filing text + news + numeric context for one asset. Best-effort:
    a missing source yields None / empty, never an error."""
    filing_form, filing_date, filing_excerpt = _load_latest_filing(
        conn, asset.asset_id, filing_char_budget
    )
    news = NewsRepository(conn).load_for_asset(asset.asset_id, limit=news_limit)
    valuation = ValuationSnapshotRepository(conn).get(asset.asset_id, as_of)
    funds = FundamentalsRepository(conn)
    fundamentals = {
        label: funds.get_latest_metric(asset.asset_id, metric)
        for label, metric in _FUNDAMENTAL_METRICS.items()
    }
    return ThesisEvidence(
        filing_excerpt=filing_excerpt,
        filing_form=filing_form,
        filing_date=filing_date,
        news=news,
        valuation=valuation,
        fundamentals=fundamentals,
    )


def _load_latest_filing(
    conn: duckdb.DuckDBPyConnection, asset_id: str, char_budget: int
) -> tuple[str | None, date | None, str | None]:
    row = conn.execute(
        """
        SELECT d.form_type, d.filed_date, t.text
        FROM disclosure_texts t
        JOIN disclosures d
          ON d.asset_id = t.asset_id AND d.accession_number = t.accession_number
        WHERE t.asset_id = ? AND t.status = 'fetched' AND length(t.text) > 0
        ORDER BY d.filed_date DESC
        LIMIT 1
        """,
        [asset_id],
    ).fetchone()
    if row is None:
        return None, None, None
    form_type, filed_date, text = row
    excerpt = text[:char_budget] if text else None
    return form_type, filed_date, excerpt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thesis_grader.py -k assemble_thesis -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/research/thesis_evidence.py tests/test_thesis_grader.py
git commit -m "✨ feat: add thesis evidence assembler (filing+news+numbers) (C2)"
```

---

### Task 4: Prompt builder

**Files:**
- Create: `croesus/research/thesis_prompt.py`
- Test: `tests/test_thesis_grader.py`

**Context:** Renders the assembled evidence into the `[system, user]` message list `ChatClient.chat` expects. The system message is the grading rubric: the four dimensions with their EXACT allowed values, the evidence-enforcement rule (cite the filing or mark `general_knowledge`), the mandatory bear case, and a strict-JSON output contract whose keys are exactly what `parse_thesis_payload` validates. The user message renders the asset header + filing excerpt + news headlines + numeric context.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_thesis_grader.py
def test_build_thesis_messages_includes_rubric_and_evidence() -> None:
    from croesus.assets.models import Asset
    from croesus.news.models import NewsItem
    from croesus.research.thesis_evidence import ThesisEvidence
    from croesus.research.thesis_prompt import build_thesis_messages

    asset = Asset(
        asset_id="US_EQ_AAPL", symbol="AAPL", name="Apple Inc.",
        asset_type="equity", sector="Tech", industry="Hardware",
    )
    ev = ThesisEvidence(
        filing_excerpt="We face intense competition.", filing_form="10-K",
        filing_date=date(2026, 5, 1),
        news=[NewsItem(
            item_id="i1", source="gdelt", external_id="u1", url="u1",
            headline="Apple launches X", summary="A summary.", body=None,
            published_at=None, source_name="reuters.com", category=None,
        )],
        valuation=None, fundamentals={"revenue": 1.0e11, "free_cash_flow": None},
    )
    messages = build_thesis_messages(asset, ev)

    assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
    system = messages[0]["content"]
    # Rubric must name every allowed value so the model stays in-vocabulary.
    for token in ("wide", "narrow", "secular_growth", "disruption", "bear_case",
                  "general_knowledge", "JSON"):
        assert token in system
    user = messages[1]["content"]
    assert "Apple Inc." in user
    assert "10-K" in user and "We face intense competition." in user
    assert "Apple launches X" in user
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_thesis_grader.py -k build_thesis -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# croesus/research/thesis_prompt.py
from __future__ import annotations

from croesus.assets.models import Asset
from croesus.research.thesis_evidence import ThesisEvidence

_SYSTEM_PROMPT = """You are an equity analyst grading the structural thesis of a \
company from its SEC filing, recent news, and numbers. Grade FOUR dimensions, \
each on a fixed scale — use ONLY these values:

- moat (durable competitive advantage): wide | narrow | none
- tech (technology capability vs peers): leading | parity | lagging
- sector (sector trajectory): secular_growth | stable | declining
- disruption (risk of being disrupted): low | medium | high

Rules:
- Base every grade on the evidence provided. For each dimension give a one- to \
two-sentence `*_evidence` that cites the filing or news where possible.
- Set `evidence_source` to "filing" only if the grades are defensible from the \
filing text; otherwise "general_knowledge".
- Always give a `bear_case`: the single most credible way this thesis is wrong.
- Give an overall `confidence`: high | medium | low.

Respond with ONE JSON object and nothing else, exactly these keys:
{
  "moat_grade": "...", "moat_evidence": "...",
  "tech_grade": "...", "tech_evidence": "...",
  "sector_grade": "...", "sector_evidence": "...",
  "disruption_grade": "...", "disruption_evidence": "...",
  "bear_case": "...", "confidence": "...", "evidence_source": "..."
}"""


def build_thesis_messages(asset: Asset, evidence: ThesisEvidence) -> list[dict[str, str]]:
    """Render the grading rubric (system) and the assembled evidence (user)."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _render_evidence(asset, evidence)},
    ]


def _render_evidence(asset: Asset, ev: ThesisEvidence) -> str:
    lines: list[str] = [
        f"Company: {asset.name or asset.symbol} ({asset.symbol})",
        f"Sector: {asset.sector or 'n/a'} | Industry: {asset.industry or 'n/a'}",
        "",
    ]

    if ev.valuation is not None:
        v = ev.valuation
        lines += [
            "Valuation snapshot:",
            f"  intrinsic_value_per_share={v.intrinsic_value_per_share} "
            f"current_price={v.current_price} upside_pct={v.upside_pct}",
            "",
        ]

    nums = ", ".join(f"{k}={v}" for k, v in ev.fundamentals.items())
    lines += [f"Key fundamentals: {nums}", ""]

    if ev.news:
        lines.append("Recent news:")
        for n in ev.news:
            headline = n.headline or "(no headline)"
            lines.append(f"  - {headline} [{n.source_name or n.source}]")
        lines.append("")

    if ev.filing_excerpt:
        lines += [
            f"Latest filing ({ev.filing_form}, filed {ev.filing_date}) — excerpt:",
            ev.filing_excerpt,
        ]
    else:
        lines.append("No filing text available.")

    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thesis_grader.py -k build_thesis -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/research/thesis_prompt.py tests/test_thesis_grader.py
git commit -m "✨ feat: add thesis prompt builder with grading rubric (C2)"
```

---

### Task 5: Thesis-grade repository (idempotent upsert)

**Files:**
- Create: `croesus/research/thesis_repository.py`
- Test: `tests/test_thesis_grader.py`

**Context:** Natural-key upsert on `(asset_id, as_of_date)` so re-grading a candidate on the same day overwrites in place (the disclosures/events/news pattern), NOT the append-only `research_notes` pattern. Depends on the `thesis_grades` table from Task 6 — but the test migrates the schema, so Task 6's schema edit is a prerequisite. **Do Task 6's schema edit (Step 3 of Task 6) before running this task's tests, OR reorder: apply the `schema.sql` change first.** To keep tasks self-contained, this task includes the schema DDL it needs inline; if Task 6 hasn't run yet, add the table to `schema.sql` now (identical DDL appears in Task 6).

- [ ] **Step 1: Add the `thesis_grades` table to `croesus/db/schema.sql`** (if not already present from Task 6)

Append near the other opportunity-engine tables (after `news_item_assets`):

```sql
-- Phase C2 (opportunity engine): LLM structural-thesis grades. One CURRENT row
-- per (asset_id, as_of_date) — re-grading overwrites. The grader reads
-- disclosure_texts + news_items + valuation_snapshots and emits discrete grades
-- with evidence; grade → DcfKnobs mapping is deterministic Phase-C3 code, never
-- LLM output. A 'failed' row carries `error` with the grade fields NULL.
CREATE TABLE IF NOT EXISTS thesis_grades (
  asset_id            TEXT NOT NULL,
  as_of_date          DATE NOT NULL,
  run_id              TEXT NOT NULL,
  model               TEXT NOT NULL,
  status              TEXT NOT NULL,   -- 'generated' | 'failed'
  moat_grade          TEXT,            -- 'wide' | 'narrow' | 'none'
  moat_evidence       TEXT,
  tech_grade          TEXT,            -- 'leading' | 'parity' | 'lagging'
  tech_evidence       TEXT,
  sector_grade        TEXT,            -- 'secular_growth' | 'stable' | 'declining'
  sector_evidence     TEXT,
  disruption_grade    TEXT,            -- 'low' | 'medium' | 'high'
  disruption_evidence TEXT,
  bear_case           TEXT,
  confidence          TEXT,            -- 'high' | 'medium' | 'low'
  evidence_source     TEXT,            -- 'filing' | 'general_knowledge'
  error               TEXT,
  metadata            JSON,
  created_at          TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, as_of_date)
);
```

- [ ] **Step 2: Write the failing test**

```python
# add to tests/test_thesis_grader.py
def test_thesis_repository_upserts_idempotently(tmp_path: Path) -> None:
    from croesus.research.thesis_models import STATUS_GENERATED, ThesisGrade
    from croesus.research.thesis_repository import ThesisGradeRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 19)
    base = dict(
        asset_id="US_EQ_AAPL", as_of_date=asof, run_id="r1", model="qwen3:32b",
        status=STATUS_GENERATED, moat_grade="narrow", moat_evidence="e",
        tech_grade="parity", tech_evidence="e", sector_grade="stable",
        sector_evidence="e", disruption_grade="medium", disruption_evidence="e",
        bear_case="b", confidence="medium", evidence_source="filing",
    )
    with get_connection(db_path) as conn:
        repo = ThesisGradeRepository(conn)
        repo.upsert(ThesisGrade(**base))
        # Re-grade same (asset, date): promote moat to wide, run r2.
        repo.upsert(ThesisGrade(**{**base, "moat_grade": "wide", "run_id": "r2"}))

        assert conn.execute("SELECT count(*) FROM thesis_grades").fetchone()[0] == 1
        loaded = repo.load_for_asset("US_EQ_AAPL", asof)
        assert loaded is not None
        assert loaded.moat_grade == "wide" and loaded.run_id == "r2"
        assert loaded.disruption_grade == "medium"
        assert repo.load_for_asset("US_EQ_AAPL", date(2026, 1, 1)) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_thesis_grader.py -k thesis_repository -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write minimal implementation**

```python
# croesus/research/thesis_repository.py
from __future__ import annotations

import json
from datetime import date

import duckdb

from croesus.research.thesis_models import ThesisGrade

_COLUMNS = (
    "asset_id", "as_of_date", "run_id", "model", "status",
    "moat_grade", "moat_evidence", "tech_grade", "tech_evidence",
    "sector_grade", "sector_evidence", "disruption_grade", "disruption_evidence",
    "bear_case", "confidence", "evidence_source", "error", "metadata",
)


class ThesisGradeRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, grade: ThesisGrade) -> None:
        """Insert or overwrite the current grade for (asset_id, as_of_date)."""
        self.conn.execute(
            """
            INSERT INTO thesis_grades (
              asset_id, as_of_date, run_id, model, status,
              moat_grade, moat_evidence, tech_grade, tech_evidence,
              sector_grade, sector_evidence, disruption_grade, disruption_evidence,
              bear_case, confidence, evidence_source, error, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, as_of_date) DO UPDATE SET
              run_id = excluded.run_id,
              model = excluded.model,
              status = excluded.status,
              moat_grade = excluded.moat_grade,
              moat_evidence = excluded.moat_evidence,
              tech_grade = excluded.tech_grade,
              tech_evidence = excluded.tech_evidence,
              sector_grade = excluded.sector_grade,
              sector_evidence = excluded.sector_evidence,
              disruption_grade = excluded.disruption_grade,
              disruption_evidence = excluded.disruption_evidence,
              bear_case = excluded.bear_case,
              confidence = excluded.confidence,
              evidence_source = excluded.evidence_source,
              error = excluded.error,
              metadata = excluded.metadata
            """,
            [
                grade.asset_id, grade.as_of_date, grade.run_id, grade.model,
                grade.status, grade.moat_grade, grade.moat_evidence,
                grade.tech_grade, grade.tech_evidence, grade.sector_grade,
                grade.sector_evidence, grade.disruption_grade,
                grade.disruption_evidence, grade.bear_case, grade.confidence,
                grade.evidence_source, grade.error, json.dumps(grade.metadata),
            ],
        )

    def load_for_asset(self, asset_id: str, as_of: date) -> ThesisGrade | None:
        row = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM thesis_grades "
            "WHERE asset_id = ? AND as_of_date = ?",
            [asset_id, as_of],
        ).fetchone()
        if row is None:
            return None
        data = dict(zip(_COLUMNS, row))
        meta = data.pop("metadata")
        return ThesisGrade(
            metadata=json.loads(meta) if isinstance(meta, str) else (meta or {}),
            **data,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_thesis_grader.py -k thesis_repository -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add croesus/db/schema.sql croesus/research/thesis_repository.py tests/test_thesis_grader.py
git commit -m "🗃️ feat: add thesis_grades table and idempotent repository (C2)"
```

---

### Task 6: The grader — funnel, LLM call, isolation

**Files:**
- Create: `croesus/research/thesis_grader.py`
- Test: `tests/test_thesis_grader.py`

**Context:** Ties it together. Funnel: distinct asset_ids with an event on `as_of_date`. For each, assemble evidence → build messages → `client.chat` → `parse_thesis_payload` → upsert a `generated` grade. Failure contract mirrors `agent.py`: `LlmUnavailable` sets `skipped_reason` and breaks the loop (server down — pointless to continue); `LlmError` or parse `ValueError` persists a `failed` grade and continues. `run_id` and `as_of_date` default to deterministic-ish values from args (NOT `Date.now`/`uuid4` at import); the caller (local_sync runner) passes them.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_thesis_grader.py
_GRADER_RESPONSE = (
    '{"moat_grade": "wide", "moat_evidence": "e1", '
    '"tech_grade": "leading", "tech_evidence": "e2", '
    '"sector_grade": "secular_growth", "sector_evidence": "e3", '
    '"disruption_grade": "low", "disruption_evidence": "e4", '
    '"bear_case": "platform shift", "confidence": "high", '
    '"evidence_source": "filing"}'
)


def _seed_candidate(conn, asset_id: str, symbol: str, asof: date) -> None:
    from croesus.assets.models import Asset
    from croesus.assets.repository import AssetRepository

    AssetRepository(conn).upsert_many([Asset(
        asset_id=asset_id, symbol=symbol, name=f"{symbol} Inc.", asset_type="equity",
    )])
    conn.execute(
        "INSERT INTO events (asset_id, as_of_date, event_type, direction, "
        "magnitude, detail, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [asset_id, asof, "abnormal_volume", "up", 2.5, "spike", "prices_daily"],
    )


def test_grade_theses_grades_candidates_and_isolates(tmp_path: Path) -> None:
    from croesus.research.thesis_grader import grade_theses
    from croesus.research.thesis_models import STATUS_FAILED, STATUS_GENERATED
    from croesus.research.thesis_repository import ThesisGradeRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 19)

    class FakeChatClient:
        base_url = "x"
        model = "fake"

        def chat(self, messages):
            # AAA candidate succeeds; BBB candidate triggers a per-request failure.
            if "BBB" in messages[1]["content"]:
                from croesus.research.llm_client import LlmError
                raise LlmError("boom")
            return _GRADER_RESPONSE

    with get_connection(db_path) as conn:
        _seed_candidate(conn, "US_EQ_AAA", "AAA", asof)
        _seed_candidate(conn, "US_EQ_BBB", "BBB", asof)
        # An asset with NO event must not be graded.
        from croesus.assets.models import Asset
        from croesus.assets.repository import AssetRepository
        AssetRepository(conn).upsert_many([Asset(
            asset_id="US_EQ_CCC", symbol="CCC", name="CCC Inc.", asset_type="equity",
        )])

        result = grade_theses(
            conn, run_id="run-1", as_of_date=asof, client=FakeChatClient()
        )
        repo = ThesisGradeRepository(conn)
        aaa = repo.load_for_asset("US_EQ_AAA", asof)
        bbb = repo.load_for_asset("US_EQ_BBB", asof)

    assert result.generated == 1 and result.failed == 1
    assert result.skipped_reason is None
    assert aaa.status == STATUS_GENERATED and aaa.moat_grade == "wide"
    assert bbb.status == STATUS_FAILED and bbb.error and bbb.moat_grade is None
    assert repo.load_for_asset("US_EQ_CCC", asof) is None  # no event → not graded


def test_grade_theses_aborts_when_llm_unavailable(tmp_path: Path) -> None:
    from croesus.research.llm_client import LlmUnavailable
    from croesus.research.thesis_grader import grade_theses

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 19)

    class DeadClient:
        base_url = "x"
        model = "fake"

        def chat(self, messages):
            raise LlmUnavailable("server down")

    with get_connection(db_path) as conn:
        _seed_candidate(conn, "US_EQ_AAA", "AAA", asof)
        result = grade_theses(conn, run_id="r", as_of_date=asof, client=DeadClient())
        n = conn.execute("SELECT count(*) FROM thesis_grades").fetchone()[0]

    assert result.skipped_reason == "server down"
    assert result.generated == 0 and result.failed == 0 and n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_thesis_grader.py -k grade_theses -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# croesus/research/thesis_grader.py
from __future__ import annotations

from datetime import date
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.research.llm_client import ChatClient, LlmError, LlmUnavailable
from croesus.research.thesis_evidence import assemble_thesis_evidence
from croesus.research.thesis_models import (
    STATUS_FAILED,
    STATUS_GENERATED,
    ThesisGrade,
    ThesisRunResult,
)
from croesus.research.thesis_parse import parse_thesis_payload
from croesus.research.thesis_prompt import build_thesis_messages
from croesus.research.thesis_repository import ThesisGradeRepository


def grade_theses(
    conn: duckdb.DuckDBPyConnection,
    *,
    run_id: str,
    as_of_date: date,
    client: ChatClient | None = None,
    log: Callable[[str], None] = print,
) -> ThesisRunResult:
    """Grade the structural thesis of every event-prefiltered candidate.

    Funnel = assets with an event on ``as_of_date`` (LLM only on the shortlist).
    Failure contract: ``LlmUnavailable`` stops the whole run (server down);
    ``LlmError`` / unparseable response persists a ``failed`` grade and continues.
    """
    if client is None:
        from croesus.research.llm_client import ChatCompletionsClient

        client = ChatCompletionsClient()

    result = ThesisRunResult(run_id=run_id)
    repo = ThesisGradeRepository(conn)

    candidate_ids = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT asset_id FROM events WHERE as_of_date = ? ORDER BY asset_id",
            [as_of_date],
        ).fetchall()
    ]
    if not candidate_ids:
        log("thesis_grader: no event candidates")
        return result

    assets_by_id = {a.asset_id: a for a in AssetRepository(conn).list_active()}

    for asset_id in candidate_ids:
        asset = assets_by_id.get(asset_id)
        if asset is None:
            continue  # candidate not in the active universe (e.g. delisted)
        try:
            evidence = assemble_thesis_evidence(conn, asset, as_of_date)
            messages = build_thesis_messages(asset, evidence)
            raw = client.chat(messages)
        except LlmUnavailable as exc:
            result.skipped_reason = str(exc)
            log(f"thesis_grader: LLM unavailable, aborting: {exc}")
            break
        except LlmError as exc:
            repo.upsert(_failed_grade(asset_id, as_of_date, run_id, client.model, str(exc)))
            result.failed += 1
            log(f"thesis_grader: failed {asset.symbol}: {exc}")
            continue

        try:
            payload = parse_thesis_payload(raw)
        except ValueError as exc:
            repo.upsert(_failed_grade(
                asset_id, as_of_date, run_id, client.model,
                f"unparseable model response: {exc}",
            ))
            result.failed += 1
            log(f"thesis_grader: unparseable {asset.symbol}: {exc}")
            continue

        grade = ThesisGrade(
            asset_id=asset_id, as_of_date=as_of_date, run_id=run_id,
            model=client.model, status=STATUS_GENERATED, **payload,
        )
        repo.upsert(grade)
        result.grades.append(grade)
        result.generated += 1
        log(f"thesis_grader: graded {asset.symbol} moat={grade.moat_grade}")

    return result


def _failed_grade(
    asset_id: str, as_of_date: date, run_id: str, model: str, error: str
) -> ThesisGrade:
    return ThesisGrade(
        asset_id=asset_id, as_of_date=as_of_date, run_id=run_id,
        model=model, status=STATUS_FAILED, error=error,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_thesis_grader.py -k grade_theses -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/research/thesis_grader.py tests/test_thesis_grader.py
git commit -m "✨ feat: add thesis grader with candidate funnel and LLM isolation (C2)"
```

---

### Task 7: Wire into local_sync

**Files:**
- Modify: `croesus/jobs/run_status.py` (add `DomainSpec`)
- Modify: `croesus/jobs/local_sync.py` (add `_run_thesis_grader` + `SyncJob`)
- Test: `tests/test_thesis_grader.py` and `tests/test_local_sync.py`

**Context:** The grader job (`thesis_grader_run`, domain `thesis_grades`) hard-depends on `event_scan` (no candidates without the events table) and soft-depends on the three evidence jobs so a fresh filing or news triggers a re-grade. Freshness keyed to job success via `_job_success_date_fn` (sparse writes). The runner constructs `run_id`/`as_of_date` and calls `grade_theses`.

- [ ] **Step 1: Add the `DomainSpec` to `croesus/jobs/run_status.py`**

Find the `news_gdelt` `DomainSpec` in the domain registry and add immediately after it:

```python
        DomainSpec(
            "thesis_grades", "thesis_grader_run", 48.0,
            _job_success_date_fn("thesis_grader_run"),
        ),
```

- [ ] **Step 2: Write the failing registration test**

```python
# add to tests/test_thesis_grader.py
def test_thesis_grader_registered_in_sync_pipeline() -> None:
    from croesus.jobs.local_sync import default_sync_jobs
    from croesus.jobs.run_status import DOMAINS_BY_NAME

    assert "thesis_grades" in DOMAINS_BY_NAME
    assert DOMAINS_BY_NAME["thesis_grades"].job_name == "thesis_grader_run"

    jobs = {job.name: job for job in default_sync_jobs()}
    assert "thesis_grader_run" in jobs
    job = jobs["thesis_grader_run"]
    assert job.domains == ("thesis_grades",)
    assert job.depends_on == ("event_scan",)
    assert job.soft_depends_on == (
        "disclosure_texts_run", "news_finnhub_run", "news_gdelt_run",
    )
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_thesis_grader.py -k registered -v`
Expected: FAIL (job not registered yet)

- [ ] **Step 4: Add the runner + SyncJob to `croesus/jobs/local_sync.py`**

Add the runner next to `_run_news_gdelt`:

```python
def _run_thesis_grader(db: Path) -> str:
    from datetime import date
    from uuid import uuid4

    from croesus.research.thesis_grader import grade_theses

    with get_connection(db) as conn:
        result = grade_theses(
            conn, run_id=uuid4().hex, as_of_date=date.today()
        )
    skipped = f" skipped={result.skipped_reason}" if result.skipped_reason else ""
    return (
        f"thesis_grader generated={result.generated} "
        f"failed={result.failed}{skipped}"
    )
```

Add the `SyncJob` to `default_sync_jobs()` after the `news_gdelt_run` job:

```python
        SyncJob(
            "thesis_grader_run", ("thesis_grades",), _run_thesis_grader,
            depends_on=("event_scan",),
            soft_depends_on=(
                "disclosure_texts_run", "news_finnhub_run", "news_gdelt_run",
            ),
        ),
```

- [ ] **Step 5: Run the registration test to verify it passes**

Run: `python -m pytest tests/test_thesis_grader.py -k registered -v`
Expected: PASS

- [ ] **Step 6: Update the ordered-jobs list in `tests/test_local_sync.py`**

Find the test that asserts the ordered list of job names (it currently contains `"news_gdelt_run"`). Add `"thesis_grader_run"` immediately after `"news_gdelt_run"` so the ordering assertion still passes. Run `python -m pytest tests/test_local_sync.py -v` and read the failure to confirm the exact expected position, then update the list.

- [ ] **Step 7: Run the sync suite**

Run: `python -m pytest tests/test_local_sync.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add croesus/jobs/run_status.py croesus/jobs/local_sync.py tests/test_thesis_grader.py tests/test_local_sync.py
git commit -m "✨ feat: wire thesis grader into local_sync pipeline (C2)"
```

---

### Task 8: Full regression

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: PASS — all prior tests (537) plus the new `test_thesis_grader.py` tests, zero regressions. If `test_local_sync.py` ordering fails, fix the expected list position from Task 7 Step 6.

- [ ] **Step 2: Confirm no DCF wiring leaked in**

Run: `git diff main...HEAD -- croesus/factors/equity/`
Expected: EMPTY — C2 must not touch `compute_valuation.py` or `valuation.py` (that is Phase C3).

---

## Self-Review

**Spec coverage:** §방법론 A four dimensions (moat/tech/sector/disruption) with discrete values → Task 1 taxonomies + Task 4 rubric + Task 2 validation. Evidence enforcement (cite filing or label general_knowledge) → Task 2 `_require_str` + `evidence_source` validation + Task 4 rubric. Bear case always present → Task 2 requires `bear_case`. Funnel (LLM on shortlist only) → Task 6 events-distinct query. Idempotent persistence → Task 5 natural-key upsert. Never-block contract → Task 6 LlmUnavailable/LlmError isolation.

**Type consistency:** `ThesisGrade` field names are identical across Task 1 (definition), Task 5 (`_COLUMNS` + upsert params), and Task 6 (`**payload` construction). `parse_thesis_payload` returns exactly the keys `ThesisGrade(**payload, ...)` consumes (the four `*_grade` + four `*_evidence` + `bear_case` + `confidence` + `evidence_source`). `ThesisEvidence` fields match between Task 3 (definition) and Task 4 (`_render_evidence`).

**Placeholder scan:** none — every step shows complete code.

**C3 boundary:** Task 8 Step 2 asserts no `factors/equity/` changes. The grade vocabularies (Task 1) are the exact keys C3's `CAP_YEARS` / `TERMINAL_GROWTH` / `RISK_PREMIUM` tables will index.
