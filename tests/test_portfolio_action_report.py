from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.actions import ProposedAction
from croesus.portfolio.repository import PortfolioRepository
from croesus.reports.portfolio_action import write_portfolio_action_reports

AS_OF = date(2026, 6, 1)


def test_markdown_and_csv_reports_are_generated_from_persisted_actions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "reports.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-1",
            "default",
            "default",
            AS_OF,
            decision="rebalance_recommended",
            summary="1 action proposed.",
            macro_regime="Goldilocks",
            macro_positioning="Neutral",
            metadata={
                "latest_portfolio_snapshot_date": "2026-06-01",
                "latest_screening_run_id": "screen-1",
            },
        )
        repo.replace_proposed_actions(
            "run-1",
            [
                ProposedAction(
                    action_id="run-1-001",
                    run_id="run-1",
                    asset_id="US_EQ_NVDA",
                    sleeve_name=None,
                    action_type="trim",
                    current_weight=0.18,
                    target_weight=0.10,
                    proposed_weight=0.10,
                    estimated_trade_value=8000.0,
                    reason_codes=["POSITION_OVER_MAX"],
                    human_readable_reason="Trim US_EQ_NVDA from 18.0% to 10.0%.",
                    requires_research=False,
                    requires_user_approval=True,
                )
            ],
        )
        markdown_path, csv_path = write_portfolio_action_reports(
            conn, "run-1", reports_dir=tmp_path
        )

    markdown = markdown_path.read_text(encoding="utf-8")
    csv_text = csv_path.read_text(encoding="utf-8")
    assert markdown_path.name == "portfolio_action_2026-06-01.md"
    assert csv_path.name == "portfolio_action_2026-06-01.csv"
    assert "# Portfolio Action Report - 2026-06-01" in markdown
    assert "## Proposed Actions" in markdown
    assert "Trim US_EQ_NVDA" in markdown
    assert "POSITION_OVER_MAX" in csv_text
    assert "trim" in csv_text
