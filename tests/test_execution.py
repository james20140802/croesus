"""Sprint 013: post-approval execution — gate enforcement, fills, idempotency."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.execution.base import ExecutionBlocked, ExecutionFailed
from croesus.execution.execute import (
    execute_all_approved,
    execute_approved_action,
    list_executable_action_ids,
)
from croesus.execution.paper_broker import PaperBroker
from croesus.portfolio.actions import ProposedAction
from croesus.portfolio.approvals import approve_action, reject_action
from croesus.portfolio.holdings_from_transactions import (
    derive_holdings_from_transactions,
)
from croesus.portfolio.repository import PortfolioRepository
from croesus.portfolio.transaction_repository import TransactionRepository

AS_OF = date(2026, 6, 1)
NOW = datetime(2026, 6, 1, 12, 0, 0)


def _action(
    action_id: str = "run-1-001",
    *,
    action_type: str = "trim",
    asset_id: str | None = "US_EQ_NVDA",
    estimated_trade_value: float | None = 8_000.0,
    requires_user_approval: bool = True,
) -> ProposedAction:
    return ProposedAction(
        action_id=action_id,
        run_id="run-1",
        asset_id=asset_id,
        sleeve_name="satellite_equity",
        action_type=action_type,
        current_weight=0.18,
        target_weight=0.10,
        proposed_weight=0.10,
        estimated_trade_value=estimated_trade_value,
        reason_codes=["POSITION_OVER_MAX"],
        human_readable_reason="Trim US_EQ_NVDA from 18.0% to 10.0%.",
        requires_research=False,
        requires_user_approval=requires_user_approval,
    )


def _open(tmp_path: Path):
    db_path = tmp_path / "e.duckdb"
    migrate(db_path)
    return get_connection(db_path)


def _seed(conn, actions: list[ProposedAction]) -> None:
    AssetRepository(conn).upsert_many(
        [
            Asset(
                asset_id="US_EQ_NVDA", symbol="NVDA", name="NVIDIA Corporation",
                asset_type="equity", country="US", currency="USD",
                sector="Technology", industry="Semiconductors", source="test",
            )
        ]
    )
    conn.execute(
        "INSERT INTO prices_daily (asset_id, date, close, source) VALUES (?, ?, ?, ?)",
        ["US_EQ_NVDA", AS_OF, 160.0, "test"],
    )
    repo = PortfolioRepository(conn)
    repo.upsert_rebalance_run(
        "run-1", "default", "default", AS_OF,
        decision="rebalance_recommended", summary="test", metadata={},
    )
    repo.replace_proposed_actions("run-1", actions, now=NOW)


# ── gate enforcement: only approved + unexpired + unexecuted reach the broker ─

def test_pending_action_is_blocked(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action()])
        with pytest.raises(ExecutionBlocked, match="pending, not approved"):
            execute_approved_action(
                conn, "run-1-001", broker=PaperBroker(conn), now=NOW,
                log=lambda m: None,
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0] == 0


def test_rejected_and_expired_actions_are_blocked(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action("run-1-001"), _action("run-1-002")])
        reject_action(conn, "run-1-001", now=NOW)
        with pytest.raises(ExecutionBlocked, match="rejected"):
            execute_approved_action(
                conn, "run-1-001", broker=PaperBroker(conn), now=NOW,
                log=lambda m: None,
            )
        # Never decided; the window lapses → expired, not executable.
        late = NOW + timedelta(days=8)
        with pytest.raises(ExecutionBlocked, match="expired"):
            execute_approved_action(
                conn, "run-1-002", broker=PaperBroker(conn), now=late,
                log=lambda m: None,
            )


def test_approved_but_window_lapsed_is_blocked(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action()])
        approve_action(conn, "run-1-001", now=NOW)
        with pytest.raises(ExecutionBlocked, match="window expired"):
            execute_approved_action(
                conn, "run-1-001", broker=PaperBroker(conn),
                now=NOW + timedelta(days=8), log=lambda m: None,
            )


def test_unknown_and_non_approval_actions_are_blocked(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action(requires_user_approval=False)])
        with pytest.raises(ExecutionBlocked, match="not found"):
            execute_approved_action(
                conn, "nope", broker=PaperBroker(conn), now=NOW, log=lambda m: None
            )
        with pytest.raises(ExecutionBlocked, match="no approval record"):
            execute_approved_action(
                conn, "run-1-001", broker=PaperBroker(conn), now=NOW,
                log=lambda m: None,
            )


# ── happy path: fill recorded, ledger updated, idempotent ────────────────────

def test_approved_trim_fills_and_records_linked_transaction(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action()])
        approve_action(conn, "run-1-001", now=NOW)
        result = execute_approved_action(
            conn, "run-1-001", broker=PaperBroker(conn, flat_fee=1.0), now=NOW,
            log=lambda m: None,
        )
        txns = TransactionRepository(conn).transactions_for_action("run-1-001")

    [fill] = result.fills
    assert fill.side == "sell"
    assert fill.price == 160.0
    assert fill.quantity == pytest.approx(8_000.0 / 160.0)  # 50 shares

    [txn] = txns
    assert txn.transaction_type == "sell"
    assert txn.asset_id == "US_EQ_NVDA"
    assert txn.quantity == pytest.approx(50.0)
    assert txn.source == "paper_broker"
    assert txn.linked_action_id == "run-1-001"


def test_double_execution_is_blocked(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action()])
        approve_action(conn, "run-1-001", now=NOW)
        execute_approved_action(
            conn, "run-1-001", broker=PaperBroker(conn), now=NOW, log=lambda m: None
        )
        with pytest.raises(ExecutionBlocked, match="already executed"):
            execute_approved_action(
                conn, "run-1-001", broker=PaperBroker(conn), now=NOW,
                log=lambda m: None,
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0] == 1


def test_fill_feeds_the_ledger_derived_book(tmp_path) -> None:
    # Sprint 009 integration: an executed sell shows up as a short-free
    # position change in the derived holdings (cash rises, no NVDA position
    # since nothing was held — clamped sell emits a warning instead).
    with _open(tmp_path) as conn:
        _seed(conn, [_action(action_type="add")])
        approve_action(conn, "run-1-001", now=NOW)
        execute_approved_action(
            conn, "run-1-001", broker=PaperBroker(conn), now=NOW, log=lambda m: None
        )
        txns = TransactionRepository(conn).list_transactions("default")
        derived = derive_holdings_from_transactions(
            txns, portfolio_id="default", as_of_date=AS_OF
        )

    nvda = next(h for h in derived.holdings if h.asset_id == "US_EQ_NVDA")
    assert nvda.quantity == pytest.approx(50.0)
    assert nvda.cost_basis == pytest.approx(8_000.0)


def test_dry_run_plans_but_records_nothing(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action()])
        approve_action(conn, "run-1-001", now=NOW)
        result = execute_approved_action(
            conn, "run-1-001", broker=PaperBroker(conn), now=NOW, dry_run=True,
            log=lambda m: None,
        )
        count = conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0]

    assert result.dry_run is True
    [order] = result.planned
    assert order.side == "sell" and order.notional == 8_000.0
    assert not result.fills
    assert count == 0


# ── non-order proposals and failures ─────────────────────────────────────────

def test_sleeve_level_action_is_skipped_not_executed(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action(action_type="rebalance_to_band", asset_id=None)])
        approve_action(conn, "run-1-001", now=NOW)
        result = execute_approved_action(
            conn, "run-1-001", broker=PaperBroker(conn), now=NOW, log=lambda m: None
        )

    assert not result.fills
    assert "describe a target, not an order" in result.skipped["run-1-001"]


def test_missing_price_raises_execution_failed(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(conn, [_action()])
        conn.execute("DELETE FROM prices_daily")
        approve_action(conn, "run-1-001", now=NOW)
        with pytest.raises(ExecutionFailed, match="no stored price"):
            execute_approved_action(
                conn, "run-1-001", broker=PaperBroker(conn), now=NOW,
                log=lambda m: None,
            )
        assert conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0] == 0


# ── --all path ────────────────────────────────────────────────────────────────

def test_execute_all_runs_only_executable_approved_actions(tmp_path) -> None:
    with _open(tmp_path) as conn:
        _seed(
            conn,
            [
                _action("run-1-001"),                      # approved → fills
                _action("run-1-002"),                      # stays pending
                _action("run-1-003", action_type="raise_cash", asset_id=None),
            ],
        )
        approve_action(conn, "run-1-001", now=NOW)
        approve_action(conn, "run-1-003", now=NOW)

        ids = list_executable_action_ids(conn, now=NOW)
        result = execute_all_approved(
            conn, broker=PaperBroker(conn), now=NOW, log=lambda m: None
        )

    assert ids == ["run-1-001", "run-1-003"]  # pending one excluded
    assert [f.action_id for f in result.fills] == ["run-1-001"]
    assert "run-1-003" in result.skipped  # sleeve-level, needs manual breakdown
