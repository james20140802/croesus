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
