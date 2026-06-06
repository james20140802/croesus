from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.actions import ProposedAction, RebalanceRunResult
from croesus.portfolio.repository import PortfolioRepository

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


def test_portfolio_repository_persists_rebalance_run_and_actions(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"
    migrate(db_path)
    action = _action(
        "act-1",
        "run-1",
        action_type="trim",
        asset_id="US_EQ_NVDA",
        current_weight=0.18,
        proposed_weight=0.10,
        estimated_trade_value=8000.0,
        reason_codes=["POSITION_OVER_MAX"],
    )

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
            metadata={"screening_run_id": "screen-1"},
        )
        repo.replace_proposed_actions("run-1", [action])
        loaded = repo.get_rebalance_run("run-1")

    assert loaded is not None
    assert loaded["run_id"] == "run-1"
    assert loaded["decision"] == "rebalance_recommended"
    assert loaded["metadata"] == {"screening_run_id": "screen-1"}
    assert loaded["actions"] == [action]


def test_load_latest_rebalance_run_prefers_newest_date(tmp_path: Path) -> None:
    db_path = tmp_path / "rebalance.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_rebalance_run(
            "run-old",
            "default",
            "default",
            date(2026, 5, 31),
            decision="no_action",
            summary="No action.",
            metadata={},
        )
        repo.replace_proposed_actions(
            "run-old",
            [_action("act-old", "run-old", action_type="hold")],
        )
        repo.upsert_rebalance_run(
            "run-new",
            "default",
            "default",
            AS_OF,
            decision="rebalance_recommended",
            summary="1 action proposed.",
            metadata={},
        )
        repo.replace_proposed_actions(
            "run-new",
            [_action("act-new", "run-new", action_type="raise_cash")],
        )
        loaded = repo.load_latest_rebalance_run("default")

    assert loaded is not None
    assert loaded["run_id"] == "run-new"
    assert [action.action_id for action in loaded["actions"]] == ["act-new"]


def _action(
    action_id: str,
    run_id: str,
    *,
    action_type: str,
    asset_id: str | None = None,
    sleeve_name: str | None = None,
    current_weight: float | None = None,
    target_weight: float | None = None,
    proposed_weight: float | None = None,
    estimated_trade_value: float | None = None,
    reason_codes: list[str] | None = None,
    human_readable_reason: str = "Reason.",
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        run_id=run_id,
        asset_id=asset_id,
        sleeve_name=sleeve_name,
        action_type=action_type,
        current_weight=current_weight,
        target_weight=target_weight,
        proposed_weight=proposed_weight,
        estimated_trade_value=estimated_trade_value,
        reason_codes=reason_codes or ["NO_ACTION_WITHIN_POLICY"],
        human_readable_reason=human_readable_reason,
        requires_research=False,
        requires_user_approval=True,
    )
