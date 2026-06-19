from datetime import date

import pytest


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


def test_parse_thesis_payload_ignores_trailing_prose_with_braces() -> None:
    # raw_decode must stop at the end of the first object even when the model
    # appends prose containing a stray "}" that a last-"}" scan would mis-grab.
    from croesus.research.thesis_parse import parse_thesis_payload

    raw = _VALID_PAYLOAD.rstrip() + "\n\nNote: see ref {500} for context."
    data = parse_thesis_payload(raw)
    assert data["moat_grade"] == "wide" and data["confidence"] == "high"


def test_parse_thesis_payload_ignores_leading_prose_with_braces() -> None:
    # A stray "{" in prose BEFORE the real object must not derail extraction.
    from croesus.research.thesis_parse import parse_thesis_payload

    raw = "For {AAPL}: my assessment is " + _VALID_PAYLOAD
    data = parse_thesis_payload(raw)
    assert data["moat_grade"] == "wide" and data["sector_grade"] == "secular_growth"


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
        assert repo.load_for_asset("US_EQ_CCC", asof) is None  # no event → not graded

    assert result.generated == 1 and result.failed == 1
    assert result.skipped_reason is None
    assert aaa.status == STATUS_GENERATED and aaa.moat_grade == "wide"
    assert bbb.status == STATUS_FAILED and bbb.error and bbb.moat_grade is None


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


def test_grade_theses_counts_candidates_missing_from_universe(tmp_path: Path) -> None:
    # An event for an asset_id with no active asset row (e.g. delisted) must be
    # counted as skipped, not silently vanish from telemetry.
    from croesus.research.thesis_grader import grade_theses

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 19)

    class UnusedClient:
        base_url = "x"
        model = "fake"

        def chat(self, messages):
            raise AssertionError("must not be called for a ghost candidate")

    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO events (asset_id, as_of_date, event_type, direction, "
            "magnitude, detail, source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["US_EQ_GHOST", asof, "abnormal_volume", "up", 2.0, "x", "prices_daily"],
        )
        result = grade_theses(conn, run_id="r", as_of_date=asof, client=UnusedClient())

    assert result.skipped == 1
    assert result.generated == 0 and result.failed == 0


def test_grade_theses_defaults_to_latest_event_cohort(tmp_path: Path) -> None:
    # With no explicit as_of_date the grader must grade MAX(events.as_of_date),
    # so a weekend run grades the last trading day's events, not today's empty date.
    from croesus.research.thesis_grader import grade_theses

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    friday = date(2026, 6, 19)

    class OkClient:
        base_url = "x"
        model = "fake"

        def chat(self, messages):
            return _GRADER_RESPONSE

    with get_connection(db_path) as conn:
        _seed_candidate(conn, "US_EQ_AAA", "AAA", friday)
        result = grade_theses(conn, run_id="r1", client=OkClient())  # no as_of_date

    assert result.generated == 1


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
