from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.holdings_from_transactions import (
    derive_holdings_from_transactions,
)
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import (
    TXN_BUY,
    TXN_DEPOSIT,
    TXN_DIVIDEND,
    TXN_FEE,
    TXN_MANUAL_ADJUSTMENT,
    TXN_SELL,
    TXN_WITHDRAWAL,
    PortfolioTransaction,
    validate_transaction,
)


def _migrated(tmp_path: Path) -> Path:
    db_path = tmp_path / "txn.duckdb"
    migrate(db_path)
    return db_path


def _txn(
    txn_id: str,
    ttype: str,
    *,
    asset_id: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
    gross_amount: float | None = None,
    fees: float | None = None,
    currency: str | None = "USD",
    when: date = date(2026, 6, 1),
    portfolio_id: str = "default",
) -> PortfolioTransaction:
    return PortfolioTransaction(
        transaction_id=txn_id,
        portfolio_id=portfolio_id,
        transaction_date=when,
        transaction_type=ttype,
        asset_id=asset_id,
        quantity=quantity,
        price=price,
        gross_amount=gross_amount,
        fees=fees,
        currency=currency,
    )


# ── schema / validation ──────────────────────────────────────────────────────


def test_migrate_creates_transactions_table(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
    assert "portfolio_transactions" in tables


def test_validation_rejects_unknown_type() -> None:
    errors = validate_transaction(_txn("t1", "teleport", asset_id="A"))
    assert errors and "unknown transaction_type" in errors[0]


def test_validation_requires_quantity_and_price_for_buy() -> None:
    errors = validate_transaction(_txn("t1", TXN_BUY, asset_id="A"))
    assert any("positive quantity" in e for e in errors)
    assert any("non-negative price" in e for e in errors)


def test_validation_requires_gross_amount_for_deposit() -> None:
    errors = validate_transaction(_txn("t1", TXN_DEPOSIT))
    assert any("positive gross_amount" in e for e in errors)


def test_validation_rejects_negative_fees() -> None:
    errors = validate_transaction(
        _txn("t1", TXN_BUY, asset_id="A", quantity=1, price=10, fees=-1)
    )
    assert any("fees may not be negative" in e for e in errors)


def test_validation_accepts_well_formed_buy() -> None:
    assert validate_transaction(
        _txn("t1", TXN_BUY, asset_id="A", quantity=1, price=10)
    ) == []


# ── repository round-trip ─────────────────────────────────────────────────────


def test_record_rejects_invalid_without_writing(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        repo = TransactionRepository(conn)
        result = repo.record_transaction(_txn("bad", TXN_BUY, asset_id="A"))
        assert not result.ok
        assert result.errors
        count = conn.execute("SELECT COUNT(*) FROM portfolio_transactions").fetchone()[0]
    assert count == 0


def test_record_backfills_gross_amount(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        repo = TransactionRepository(conn)
        result = repo.record_transaction(
            _txn("buy1", TXN_BUY, asset_id="AAPL", quantity=2, price=190)
        )
        assert result.ok
        stored = repo.get_transaction("buy1")
    assert stored is not None
    assert stored.gross_amount == 2 * 190


def test_list_transactions_is_ordered_and_filterable(tmp_path: Path) -> None:
    db_path = _migrated(tmp_path)
    with get_connection(db_path) as conn:
        repo = TransactionRepository(conn)
        repo.record_transaction(
            _txn("b", TXN_BUY, asset_id="MSFT", quantity=1, price=400,
                 when=date(2026, 6, 5))
        )
        repo.record_transaction(
            _txn("a", TXN_BUY, asset_id="AAPL", quantity=1, price=190,
                 when=date(2026, 6, 1))
        )
        all_txns = repo.list_transactions("default")
        only_aapl = repo.list_transactions("default", asset_id="AAPL")
    assert [t.transaction_id for t in all_txns] == ["a", "b"]  # date order
    assert [t.asset_id for t in only_aapl] == ["AAPL"]


# ── holdings derivation ───────────────────────────────────────────────────────


def test_derive_average_cost_and_cash_after_buys() -> None:
    txns = [
        _txn("d", TXN_DEPOSIT, gross_amount=10_000, when=date(2026, 6, 1)),
        _txn("b1", TXN_BUY, asset_id="AAPL", quantity=10, price=100,
             when=date(2026, 6, 2)),
        _txn("b2", TXN_BUY, asset_id="AAPL", quantity=10, price=120,
             when=date(2026, 6, 3)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    by_id = {h.asset_id: h for h in derived.holdings}
    aapl = by_id["AAPL"]
    assert aapl.quantity == 20
    assert aapl.cost_basis == 2200  # 10*100 + 10*120
    assert aapl.avg_cost == 110
    # 10_000 deposit - 1000 - 1200 spent.
    assert by_id["CASH_USD"].quantity == 7800


def test_derive_realized_pnl_on_sell() -> None:
    txns = [
        _txn("b1", TXN_BUY, asset_id="AAPL", quantity=10, price=100,
             when=date(2026, 6, 2)),
        _txn("s1", TXN_SELL, asset_id="AAPL", quantity=4, price=150,
             when=date(2026, 6, 5)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    by_id = {h.asset_id: h for h in derived.holdings}
    # avg cost 100; sold 4 @ 150 -> realized (150-100)*4 = 200.
    assert derived.realized_pnl == 200
    assert by_id["AAPL"].quantity == 6
    assert by_id["AAPL"].cost_basis == 600  # remaining 6 @ avg 100


def test_derive_dividend_is_income_not_basis() -> None:
    txns = [
        _txn("b1", TXN_BUY, asset_id="SCHD", quantity=10, price=30,
             when=date(2026, 6, 2)),
        _txn("dv", TXN_DIVIDEND, asset_id="SCHD", gross_amount=25,
             when=date(2026, 6, 10)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    by_id = {h.asset_id: h for h in derived.holdings}
    assert derived.dividend_income == 25
    assert by_id["SCHD"].cost_basis == 300  # unchanged by the dividend
    assert by_id["CASH_USD"].quantity == 25 - 300  # +25 dividend, -300 buy


def test_derive_closed_position_is_dropped() -> None:
    txns = [
        _txn("b1", TXN_BUY, asset_id="AAPL", quantity=5, price=100,
             when=date(2026, 6, 2)),
        _txn("s1", TXN_SELL, asset_id="AAPL", quantity=5, price=110,
             when=date(2026, 6, 5)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    assert all(h.asset_id != "AAPL" for h in derived.holdings)


def test_derive_oversell_is_clamped_and_warned() -> None:
    txns = [
        _txn("b1", TXN_BUY, asset_id="AAPL", quantity=5, price=100,
             when=date(2026, 6, 2)),
        _txn("s1", TXN_SELL, asset_id="AAPL", quantity=8, price=110,
             when=date(2026, 6, 5)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    assert any("exceeds held" in w for w in derived.warnings)
    assert all(h.asset_id != "AAPL" for h in derived.holdings)  # clamped to 0


def test_derive_respects_as_of_date() -> None:
    txns = [
        _txn("b1", TXN_BUY, asset_id="AAPL", quantity=5, price=100,
             when=date(2026, 6, 2)),
        _txn("b2", TXN_BUY, asset_id="AAPL", quantity=5, price=100,
             when=date(2026, 6, 20)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 10)
    )
    by_id = {h.asset_id: h for h in derived.holdings}
    assert by_id["AAPL"].quantity == 5  # the June 20 buy is excluded


def test_derive_withdrawal_and_fee_reduce_cash() -> None:
    txns = [
        _txn("dep", TXN_DEPOSIT, gross_amount=1000, when=date(2026, 6, 1)),
        _txn("wd", TXN_WITHDRAWAL, gross_amount=200, when=date(2026, 6, 2)),
        _txn("fee", TXN_FEE, fees=5, when=date(2026, 6, 3)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    assert derived.cash_by_currency["USD"] == 1000 - 200 - 5


def test_derive_manual_adjustment_cannot_go_negative() -> None:
    # An over-large reducing adjustment clamps to the held quantity (long-only),
    # never emitting a phantom short position with a meaningless cost basis.
    txns = [
        _txn("b1", TXN_BUY, asset_id="AAPL", quantity=5, price=100,
             when=date(2026, 6, 2)),
        _txn("adj", TXN_MANUAL_ADJUSTMENT, asset_id="AAPL", quantity=-10,
             price=None, when=date(2026, 6, 5)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    assert any("exceeds held" in w for w in derived.warnings)
    # Position is closed (clamped to 0), not negative.
    assert all(h.asset_id != "AAPL" for h in derived.holdings)


def test_derive_buy_posts_cash_in_transaction_currency() -> None:
    # Cash for a buy leaves in the transaction's own currency, even when a later
    # transaction's currency differs from the position's first-seen currency.
    txns = [
        _txn("dep", TXN_DEPOSIT, gross_amount=10_000, currency="USD",
             when=date(2026, 6, 1)),
        _txn("dep_eur", TXN_DEPOSIT, gross_amount=5_000, currency="EUR",
             when=date(2026, 6, 1)),
        _txn("b1", TXN_BUY, asset_id="SAP", quantity=10, price=100,
             currency="EUR", when=date(2026, 6, 2)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    # The 1000 EUR cost is drawn from EUR cash, not USD.
    assert derived.cash_by_currency["EUR"] == 4_000
    assert derived.cash_by_currency["USD"] == 10_000


def test_derive_manual_adjustment_changes_quantity() -> None:
    txns = [
        _txn("b1", TXN_BUY, asset_id="AAPL", quantity=10, price=100,
             when=date(2026, 6, 2)),
        # 2:1 split recorded as a +10 share manual adjustment (no price).
        _txn("adj", TXN_MANUAL_ADJUSTMENT, asset_id="AAPL", quantity=10,
             price=None, when=date(2026, 6, 5)),
    ]
    derived = derive_holdings_from_transactions(
        txns, portfolio_id="default", as_of_date=date(2026, 6, 30)
    )
    by_id = {h.asset_id: h for h in derived.holdings}
    assert by_id["AAPL"].quantity == 20
    assert by_id["AAPL"].cost_basis == 1000  # basis unchanged
    assert by_id["AAPL"].avg_cost == 50  # halved by the split
