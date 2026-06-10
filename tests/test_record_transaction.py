from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.record_transaction import (
    RECORD_REJECTED,
    RECORD_UNRESOLVED_SYMBOL,
    record_manual_transaction,
)
from croesus.portfolio.models import Portfolio
from croesus.portfolio.repository import PortfolioRepository
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import TXN_BUY, TXN_DEPOSIT


def _migrated(tmp_path: Path) -> Path:
    db_path = tmp_path / "rec.duckdb"
    migrate(db_path)
    return db_path


def _seed_asset(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        """
        INSERT INTO assets (asset_id, symbol, name, asset_type, currency, is_active)
        VALUES ('US_ETF_VOO', 'VOO', 'Vanguard S&P 500', 'etf', 'USD', TRUE)
        """
    )


def _seed_portfolio(conn: duckdb.DuckDBPyConnection, base_currency: str = "USD") -> None:
    PortfolioRepository(conn).upsert_portfolio(
        Portfolio(portfolio_id="default", profile_id="default", name="d",
                  base_currency=base_currency)
    )


def test_records_self_directed_buy_resolving_symbol(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_asset(conn)
        _seed_portfolio(conn)
        result = record_manual_transaction(
            conn, transaction_type=TXN_BUY, symbol="VOO",
            quantity=2, price=670, transaction_date=date(2026, 6, 10),
        )
        assert result.ok
        assert result.transaction.asset_id == "US_ETF_VOO"  # symbol resolved
        assert result.transaction.gross_amount == 2 * 670  # backfilled
        stored = TransactionRepository(conn).list_transactions("default")
    assert len(stored) == 1
    assert stored[0].transaction_type == TXN_BUY


def test_explicit_asset_id_skips_resolution(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_portfolio(conn)  # no assets table entry needed
        result = record_manual_transaction(
            conn, transaction_type=TXN_BUY, asset_id="US_ETF_VOO",
            quantity=1, price=670,
        )
    assert result.ok
    assert result.transaction.asset_id == "US_ETF_VOO"


def test_records_cash_deposit_without_asset(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_portfolio(conn)
        result = record_manual_transaction(
            conn, transaction_type=TXN_DEPOSIT, gross_amount=5_000,
        )
    assert result.ok
    assert result.transaction.asset_id is None
    assert result.transaction.gross_amount == 5_000


def test_unresolved_symbol_is_reported_not_written(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_portfolio(conn)
        result = record_manual_transaction(
            conn, transaction_type=TXN_BUY, symbol="GHOST", quantity=1, price=10,
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0]
    assert result.status == RECORD_UNRESOLVED_SYMBOL
    assert not result.ok
    assert count == 0  # a typo must not mint a position


def test_invalid_transaction_is_rejected_without_writing(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_asset(conn)
        _seed_portfolio(conn)
        # A buy with no price fails validation in the repository.
        result = record_manual_transaction(
            conn, transaction_type=TXN_BUY, asset_id="US_ETF_VOO", quantity=2,
        )
        count = conn.execute(
            "SELECT COUNT(*) FROM portfolio_transactions"
        ).fetchone()[0]
    assert result.status == RECORD_REJECTED
    assert result.errors
    assert count == 0


def test_currency_defaults_to_portfolio_base(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_portfolio(conn, base_currency="EUR")
        result = record_manual_transaction(
            conn, transaction_type=TXN_DEPOSIT, gross_amount=1_000,
        )
    assert result.transaction.currency == "EUR"


def test_note_is_stored_in_metadata(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        _seed_portfolio(conn)
        result = record_manual_transaction(
            conn, transaction_type=TXN_DEPOSIT, gross_amount=1_000,
            note="initial funding",
        )
        stored = TransactionRepository(conn).get_transaction(
            result.transaction.transaction_id
        )
    assert stored.metadata.get("note") == "initial funding"


def test_does_not_place_broker_orders(tmp_path: Path) -> None:
    # Recording a self-directed transaction must never call an order surface.
    import croesus.jobs.record_transaction as mod

    source = Path(mod.__file__).read_text(encoding="utf-8").lower()
    for token in ("submit_order", "place_order", "execute_order", "broker_client"):
        assert token not in source
