from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.equity.band_repository import BandRow, IntrinsicValueBandRepository
from croesus.factors.equity.repository import (
    ValuationSnapshot,
    ValuationSnapshotRepository,
)
from croesus.research.thesis_models import STATUS_GENERATED, ThesisGrade
from croesus.research.thesis_repository import ThesisGradeRepository


AS_OF = date(2026, 6, 20)


class ScriptedOpportunityPrompter:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.seen: list[dict[str, Any]] = []

    def select(self, key: str, message: str, description: str, choices: list, default: Any) -> Any:
        self.seen.append(
            {
                "key": key,
                "message": message,
                "description": description,
                "choices": choices,
                "default": default,
            }
        )
        return self.answer


def _band(
    asset_id: str,
    scenario: str,
    intrinsic: float,
    current: float,
    *,
    band_date: date = AS_OF,
) -> BandRow:
    return BandRow(
        asset_id=asset_id,
        date=band_date,
        scenario=scenario,
        intrinsic_value_per_share=intrinsic,
        current_price=current,
        upside_pct=intrinsic / current - 1.0,
        wacc=0.10,
        fcf_growth_rate=0.08,
        terminal_growth_rate=0.025,
        explicit_years=7,
        wacc_risk_premium=0.01,
        moat_grade="narrow",
        sector_grade="stable",
        disruption_grade="medium",
        thesis_as_of_date=AS_OF,
        thesis_run_id="run-1",
    )


def _seed_opportunity_rows(db_path: Path) -> None:
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        ValuationSnapshotRepository(conn).upsert(
            ValuationSnapshot(
                asset_id="US_EQ_AAPL",
                date=AS_OF,
                intrinsic_value_per_share=110.0,
                current_price=100.0,
                upside_pct=0.10,
                wacc=0.10,
                fcf_growth_rate=0.08,
                terminal_growth_rate=0.025,
                assumptions={"explicit_years": 5},
            )
        )
        ThesisGradeRepository(conn).upsert(
            ThesisGrade(
                asset_id="US_EQ_AAPL",
                as_of_date=AS_OF,
                run_id="run-1",
                model="qwen3:32b",
                status=STATUS_GENERATED,
                moat_grade="narrow",
                moat_evidence="switching costs in filing",
                tech_grade="leading",
                tech_evidence="product roadmap evidence",
                sector_grade="stable",
                sector_evidence="market demand evidence",
                disruption_grade="medium",
                disruption_evidence="competitive threat evidence",
                bear_case="margin pressure invalidates the thesis",
                confidence="medium",
                evidence_source="filing",
            )
        )
        repo = IntrinsicValueBandRepository(conn)
        for scenario, intrinsic in (
            ("bear", 90.0),
            ("base", 140.0),
            ("bull", 180.0),
        ):
            repo.upsert_band(_band("US_EQ_AAPL", scenario, intrinsic, 100.0))


def test_methodology_registry_exposes_available_a_and_deferred_b() -> None:
    from croesus.opportunities.selection import OPPORTUNITY_METHODOLOGIES

    assert OPPORTUNITY_METHODOLOGIES["moat_adjusted_intrinsic_value"].available is True
    assert OPPORTUNITY_METHODOLOGIES["event_driven_thesis"].available is False


def test_select_methodology_uses_prompt_and_blocks_unavailable_choice() -> None:
    from croesus.opportunities.selection import (
        MethodologyUnavailable,
        select_methodology,
    )

    prompter = ScriptedOpportunityPrompter("moat_adjusted_intrinsic_value")

    selected = select_methodology(prompter=prompter)

    assert selected.key == "moat_adjusted_intrinsic_value"
    assert prompter.seen[0]["key"] == "methodology"
    assert prompter.seen[0]["choices"] == [
        "moat_adjusted_intrinsic_value",
        "event_driven_thesis",
    ]
    assert prompter.seen[0]["default"] == "moat_adjusted_intrinsic_value"

    try:
        select_methodology("event_driven_thesis")
    except MethodologyUnavailable as exc:
        assert "not implemented" in str(exc)
    else:  # pragma: no cover - failure path
        raise AssertionError("deferred methodology should be blocked")

    blocked_prompter = ScriptedOpportunityPrompter("event_driven_thesis")
    with pytest.raises(MethodologyUnavailable):
        select_methodology(prompter=blocked_prompter)


def test_run_opportunity_review_returns_methodology_a_cards(tmp_path: Path) -> None:
    from croesus.opportunities.review import run_opportunity_review

    db_path = tmp_path / "croesus.duckdb"
    _seed_opportunity_rows(db_path)

    with get_connection(db_path) as conn:
        result = run_opportunity_review(
            conn,
            methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=AS_OF,
        )

    assert result.methodology.key == "moat_adjusted_intrinsic_value"
    assert result.recommendation_only is True
    assert len(result.cards) == 1
    card = result.cards[0]
    assert card.asset_id == "US_EQ_AAPL"
    assert card.symbol == "AAPL"
    assert card.current_price == 100.0
    assert card.mechanical_intrinsic_value == 110.0
    assert card.band_intrinsic_by_scenario == {
        "bear": 90.0,
        "base": 140.0,
        "bull": 180.0,
    }
    assert card.base_upside_pct == pytest.approx(0.40)
    assert card.thesis_confidence == "medium"
    assert card.bear_case == "margin pressure invalidates the thesis"


def test_opportunity_review_uses_latest_complete_band_set(tmp_path: Path) -> None:
    from croesus.opportunities.review import run_opportunity_review

    db_path = tmp_path / "croesus.duckdb"
    prior = AS_OF - timedelta(days=1)
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        repo = IntrinsicValueBandRepository(conn)
        for scenario, intrinsic in (
            ("bear", 80.0),
            ("base", 130.0),
            ("bull", 170.0),
        ):
            repo.upsert_band(
                _band("US_EQ_AAPL", scenario, intrinsic, 100.0, band_date=prior)
            )
        repo.upsert_band(_band("US_EQ_AAPL", "base", 150.0, 100.0))

        result = run_opportunity_review(
            conn,
            methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=AS_OF,
        )

    card = result.cards[0]
    assert card.as_of_date == prior
    assert card.band_intrinsic_by_scenario == {
        "bear": 80.0,
        "base": 130.0,
        "bull": 170.0,
    }


def _thesis(asset_id: str, as_of: date, *, run_id: str, confidence: str) -> ThesisGrade:
    return ThesisGrade(
        asset_id=asset_id,
        as_of_date=as_of,
        run_id=run_id,
        model="qwen3:32b",
        status=STATUS_GENERATED,
        moat_grade="narrow",
        moat_evidence="switching costs",
        tech_grade="leading",
        tech_evidence="roadmap",
        sector_grade="stable",
        sector_evidence="demand",
        disruption_grade="medium",
        disruption_evidence="threat",
        bear_case="thesis breaks",
        confidence=confidence,
        evidence_source="filing",
    )


def test_opportunity_review_loads_thesis_at_band_date_not_review_date(
    tmp_path: Path,
) -> None:
    """The card's thesis must match the date the band was built from, not the
    later review date — otherwise a newer thesis would be shown against an
    older band."""
    from croesus.opportunities.review import run_opportunity_review

    db_path = tmp_path / "croesus.duckdb"
    prior = AS_OF - timedelta(days=1)
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        repo = IntrinsicValueBandRepository(conn)
        for scenario, intrinsic in (("bear", 80.0), ("base", 130.0), ("bull", 170.0)):
            repo.upsert_band(
                _band("US_EQ_AAPL", scenario, intrinsic, 100.0, band_date=prior)
            )
        thesis_repo = ThesisGradeRepository(conn)
        thesis_repo.upsert(
            _thesis("US_EQ_AAPL", prior, run_id="run-prior", confidence="low")
        )
        thesis_repo.upsert(
            _thesis("US_EQ_AAPL", AS_OF, run_id="run-new", confidence="high")
        )

        result = run_opportunity_review(
            conn,
            methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=AS_OF,
        )

    card = result.cards[0]
    assert card.thesis_as_of_date == prior
    assert card.thesis_confidence == "low"


def test_opportunity_review_sorts_equal_upside_by_symbol(tmp_path: Path) -> None:
    from croesus.opportunities.review import run_opportunity_review

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        repo = IntrinsicValueBandRepository(conn)
        for asset_id, current in (("US_EQ_AAPL", 100.0), ("US_EQ_MSFT", 200.0)):
            for scenario, intrinsic in (
                ("bear", current * 0.9),
                ("base", current * 1.4),
                ("bull", current * 1.8),
            ):
                repo.upsert_band(_band(asset_id, scenario, intrinsic, current))

        result = run_opportunity_review(
            conn,
            methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=AS_OF,
        )

    assert [card.symbol for card in result.cards] == ["AAPL", "MSFT"]


def test_opportunity_review_cli_prints_selected_methodology(
    tmp_path: Path, capsys
) -> None:
    from croesus.jobs.opportunity_review import main

    db_path = tmp_path / "croesus.duckdb"
    _seed_opportunity_rows(db_path)

    main(
        [
            "--methodology",
            "moat_adjusted_intrinsic_value",
            "--date",
            AS_OF.isoformat(),
            "--db-path",
            str(db_path),
        ]
    )

    out = capsys.readouterr().out
    assert "Moat-adjusted intrinsic value" in out
    assert "recommendation-only; no trades" in out
    assert "AAPL" in out
    assert "bear/base/bull: $90.00 / $140.00 / $180.00" in out
    assert "Thesis evidence:" in out
    assert "switching costs in filing" in out


def test_opportunity_review_cli_uses_prompt_when_methodology_omitted(
    tmp_path: Path, capsys
) -> None:
    from croesus.jobs.opportunity_review import main

    db_path = tmp_path / "croesus.duckdb"
    _seed_opportunity_rows(db_path)
    prompter = ScriptedOpportunityPrompter("moat_adjusted_intrinsic_value")

    main(
        [
            "--date",
            AS_OF.isoformat(),
            "--db-path",
            str(db_path),
        ],
        prompter=prompter,
    )

    out = capsys.readouterr().out
    assert prompter.seen[0]["key"] == "methodology"
    assert "Moat-adjusted intrinsic value" in out
    assert "AAPL" in out
