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
