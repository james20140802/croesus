from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.actions import ProposedAction, RebalanceRunResult

AS_OF = date(2026, 6, 1)


def test_migrate_creates_rebalance_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"

    migrate(db_path)

    with get_connection(db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'main'
                """
            ).fetchall()
        }

    assert {"rebalance_runs", "proposed_actions"} <= tables


def test_rebalance_action_models_capture_product_contract(tmp_path: Path) -> None:
    action = ProposedAction(
        action_id="act-1",
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
    result = RebalanceRunResult(
        run_id="run-1",
        portfolio_id="default",
        profile_id="default",
        as_of_date=AS_OF,
        decision="rebalance_recommended",
        actions=[action],
        markdown_report_path=tmp_path / "portfolio_action_2026-06-01.md",
        csv_report_path=tmp_path / "portfolio_action_2026-06-01.csv",
    )

    assert result.actions == [action]
    assert action.reason_codes == ["POSITION_OVER_MAX"]
    assert action.requires_user_approval is True
