from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.record_execution import (
    EXEC_AMBIGUOUS_DIRECTION,
    EXEC_NOT_FOUND,
    EXEC_PORTFOLIO_MISMATCH,
    record_execution,
)
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import TXN_BUY, TXN_SELL


def _migrated(tmp_path: Path) -> Path:
    db_path = tmp_path / "exec.duckdb"
    migrate(db_path)
    return db_path


def _seed_action(
    conn: duckdb.DuckDBPyConnection,
    *,
    action_id: str,
    portfolio_id: str = "default",
    asset_id: str = "AAPL",
    action_type: str = "trim",
    current_weight: float | None = 0.30,
    proposed_weight: float | None = 0.20,
) -> None:
    conn.execute(
        """
        INSERT INTO rebalance_runs (run_id, portfolio_id, profile_id, date, decision, summary)
        VALUES ('run1', ?, 'default', DATE '2026-06-10', 'rebalance', 's')
        ON CONFLICT (run_id) DO NOTHING
        """,
        [portfolio_id],
    )
    conn.execute(
        """
        INSERT INTO proposed_actions (
          action_id, run_id, asset_id, sleeve_name, action_type,
          current_weight, target_weight, proposed_weight, estimated_trade_value,
          reason_codes, human_readable_reason, requires_research, requires_user_approval
        )
        VALUES (?, 'run1', ?, NULL, ?, ?, ?, ?, 1000.0, '[]', 'r', FALSE, TRUE)
        """,
        [action_id, asset_id, action_type, current_weight, proposed_weight,
         proposed_weight],
    )


def test_records_linked_transaction_for_trim(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_action(conn, action_id="act1", action_type="trim")
        result = record_execution(
            conn, "act1", quantity=2, price=190, transaction_date=date(2026, 6, 11)
        )
        assert result.ok
        assert result.transaction.transaction_type == TXN_SELL  # trim -> sell
        assert result.transaction.linked_action_id == "act1"
        assert result.transaction.asset_id == "AAPL"

        linked = TransactionRepository(conn).transactions_for_action("act1")
    assert len(linked) == 1
    assert linked[0].quantity == 2


def test_add_action_infers_buy(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_action(conn, action_id="act2", action_type="add")
        result = record_execution(conn, "act2", quantity=1, price=300)
    assert result.ok
    assert result.transaction.transaction_type == TXN_BUY


def test_rebalance_to_band_uses_weight_direction(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        # proposed below current -> sell
        _seed_action(
            conn, action_id="down", action_type="rebalance_to_band",
            current_weight=0.4, proposed_weight=0.2,
        )
        _seed_action(
            conn, action_id="up", action_type="rebalance_to_band",
            current_weight=0.2, proposed_weight=0.4,
        )
        down = record_execution(conn, "down", quantity=1, price=100)
        up = record_execution(conn, "up", quantity=1, price=100)
    assert down.transaction.transaction_type == TXN_SELL
    assert up.transaction.transaction_type == TXN_BUY


def test_rebalance_to_band_equal_weight_is_ambiguous(tmp_path: Path) -> None:
    # proposed == current is a no-op; inference must not silently pick buy.
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_action(
            conn, action_id="flat", action_type="rebalance_to_band",
            current_weight=0.3, proposed_weight=0.3,
        )
        result = record_execution(conn, "flat", quantity=1, price=100)
    assert result.status == EXEC_AMBIGUOUS_DIRECTION
    assert not result.ok


def test_explicit_type_overrides_inference(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_action(conn, action_id="act3", action_type="trim")
        result = record_execution(
            conn, "act3", quantity=1, price=100, transaction_type=TXN_BUY
        )
    assert result.transaction.transaction_type == TXN_BUY


def test_unknown_action_is_not_found(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        result = record_execution(conn, "ghost", quantity=1, price=10)
    assert result.status == EXEC_NOT_FOUND
    assert not result.ok


def test_portfolio_mismatch_is_rejected(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_action(conn, action_id="act4", portfolio_id="default")
        result = record_execution(
            conn, "act4", quantity=1, price=10, portfolio_id="other"
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0]
    assert result.status == EXEC_PORTFOLIO_MISMATCH
    assert count == 0  # nothing written on mismatch


def test_ambiguous_direction_requires_explicit_type(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_action(conn, action_id="act5", action_type="hold")
        result = record_execution(conn, "act5", quantity=1, price=10)
    assert result.status == EXEC_AMBIGUOUS_DIRECTION
    assert not result.ok


def test_does_not_place_broker_orders(tmp_path: Path) -> None:
    # The recorder only writes a ledger row; it must not call any order-
    # submission surface. This guards the recommendation-only boundary. (The
    # word "broker" appears in prose explaining what it deliberately does *not*
    # do, so we match concrete call tokens, not documentation.)
    import croesus.jobs.record_execution as mod

    source = Path(mod.__file__).read_text(encoding="utf-8").lower()
    for token in ("submit_order", "place_order", "execute_order", "broker_client"):
        assert token not in source
