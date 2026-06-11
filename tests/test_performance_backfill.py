"""Sprint 009: transaction-driven snapshots + historical performance backfill."""
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.performance_backfill import (
    BACKFILL_SOURCE,
    run_performance_backfill,
)
from croesus.jobs.performance_check import run_performance_check
from croesus.jobs.portfolio_snapshot import NoHoldingsSource, run_portfolio_snapshot
from croesus.portfolio.performance import GOAL_INSUFFICIENT
from croesus.portfolio.repository import PortfolioRepository
from croesus.portfolio.transaction_repository import TransactionRepository
from croesus.portfolio.transactions import (
    TXN_BUY,
    TXN_DEPOSIT,
    PortfolioTransaction,
)
from croesus.profiles.seed_default_profile import seed_default_profile

START = date(2026, 4, 1)
AS_OF = date(2026, 6, 1)


def _open(tmp_path: Path):
    db_path = tmp_path / "b.duckdb"
    migrate(db_path)
    return get_connection(db_path)


def _seed_asset(conn: duckdb.DuckDBPyConnection) -> None:
    AssetRepository(conn).upsert_many(
        [
            Asset(
                asset_id="US_EQ_AAPL", symbol="AAPL", name="Apple Inc.",
                asset_type="equity", country="US", currency="USD",
                sector="Technology", industry="Consumer Electronics",
            )
        ]
    )


def _seed_prices(
    conn: duckdb.DuckDBPyConnection,
    *,
    start: date = START,
    end: date = AS_OF,
    start_close: float = 100.0,
    daily_step: float = 0.5,
) -> None:
    """Weekday closes rising linearly so period returns are deterministic."""
    rows = []
    day, close = start, start_close
    while day <= end:
        if day.weekday() < 5:
            rows.append(("US_EQ_AAPL", day, close, "test"))
            close += daily_step
        day += timedelta(days=1)
    conn.executemany(
        "INSERT INTO prices_daily (asset_id, date, close, source) VALUES (?, ?, ?, ?)",
        rows,
    )


def _record_buy(
    conn: duckdb.DuckDBPyConnection, *, quantity: float = 10.0, price: float = 100.0
) -> None:
    repo = TransactionRepository(conn)
    for txn in (
        PortfolioTransaction(
            transaction_id="t-dep", portfolio_id="default",
            transaction_date=START, transaction_type=TXN_DEPOSIT,
            gross_amount=2_000.0, currency="USD", source="test",
        ),
        PortfolioTransaction(
            transaction_id="t-buy", portfolio_id="default",
            transaction_date=START, transaction_type=TXN_BUY,
            asset_id="US_EQ_AAPL", quantity=quantity, price=price,
            currency="USD", source="test",
        ),
    ):
        result = repo.record_transaction(txn)
        assert result.status == "recorded", result.errors


# ── transaction-driven snapshot ───────────────────────────────────────────────

def test_snapshot_without_csv_derives_holdings_from_ledger(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        seed_default_profile(conn)
        _seed_asset(conn)
        _seed_prices(conn)
        _record_buy(conn)  # 10 AAPL @ 100, cash 2000 - 1000 = 1000

        result = run_portfolio_snapshot(
            conn, None, as_of_date=AS_OF, log=lambda m: None
        )
        holdings = PortfolioRepository(conn).get_holdings("default", AS_OF)

    # Jun 1 2026 is a Monday — the 44th weekday since Apr 1 → close 121.5.
    assert result.holdings_imported == 2  # AAPL + CASH_USD
    assert result.total_market_value == pytest.approx(10 * 121.5 + 1_000.0)
    assert not result.data_quality_errors
    assert {h.asset_id for h in holdings} == {"US_EQ_AAPL", "CASH_USD"}


def test_snapshot_without_csv_or_transactions_raises(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        seed_default_profile(conn)
        with pytest.raises(NoHoldingsSource, match="no transactions recorded"):
            run_portfolio_snapshot(conn, None, as_of_date=AS_OF, log=lambda m: None)


def test_csv_is_cross_checked_against_ledger(tmp_path: Path) -> None:
    csv_path = tmp_path / "h.csv"
    csv_path.write_text(
        "portfolio_id,asset_id,quantity,market_value,currency,cost_basis\n"
        "default,US_EQ_AAPL,25,2500,USD,2500\n",  # ledger says 10
        encoding="utf-8",
    )
    with _open(tmp_path) as conn:
        seed_default_profile(conn)
        _seed_asset(conn)
        _seed_prices(conn)
        _record_buy(conn, quantity=10.0)

        result = run_portfolio_snapshot(
            conn, csv_path, as_of_date=AS_OF, log=lambda m: None
        )

    reconciliation = [w for w in result.warnings if "reconciliation" in w]
    assert len(reconciliation) == 1
    assert "US_EQ_AAPL" in reconciliation[0]
    # The CSV stays authoritative for the snapshot itself.
    assert result.holdings_imported == 1


# ── performance backfill ──────────────────────────────────────────────────────

def test_backfill_reconstructs_history_idempotently(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        seed_default_profile(conn)
        _seed_asset(conn)
        _seed_prices(conn)
        _record_buy(conn)

        first = run_performance_backfill(conn, end_date=AS_OF, log=lambda m: None)
        count_after_first = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots"
        ).fetchone()[0]
        second = run_performance_backfill(conn, end_date=AS_OF, log=lambda m: None)
        count_after_second = conn.execute(
            "SELECT COUNT(*) FROM portfolio_snapshots"
        ).fetchone()[0]
        sources = conn.execute(
            "SELECT DISTINCT json_extract_string(metadata, '$.source') "
            "FROM portfolio_snapshots"
        ).fetchall()

    assert first.snapshots_written > 30  # every weekday Apr 1 .. Jun 1
    assert first.days_degraded == 0
    assert second.snapshots_written == 0
    assert second.skipped_existing == first.snapshots_written
    assert count_after_first == count_after_second == first.snapshots_written
    assert sources == [(BACKFILL_SOURCE,)]


def test_backfill_never_overwrites_live_snapshots(tmp_path: Path) -> None:
    live_day = date(2026, 4, 15)
    with _open(tmp_path) as conn:
        seed_default_profile(conn)
        _seed_asset(conn)
        _seed_prices(conn)
        _record_buy(conn)
        PortfolioRepository(conn).save_snapshot(
            "default", live_day, 99_999.0, metadata={"source": "live"}
        )

        run_performance_backfill(conn, end_date=AS_OF, log=lambda m: None)
        kept = conn.execute(
            "SELECT total_market_value FROM portfolio_snapshots "
            "WHERE portfolio_id = 'default' AND as_of_date = ?",
            [live_day],
        ).fetchone()

    assert kept == (99_999.0,)


def test_backfill_without_transactions_raises(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        seed_default_profile(conn)
        with pytest.raises(ValueError, match="no recorded transactions"):
            run_performance_backfill(conn, end_date=AS_OF, log=lambda m: None)


def test_backfilled_history_yields_one_month_return(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        seed_default_profile(conn)
        _seed_asset(conn)
        _seed_prices(conn)
        _record_buy(conn)

        run_performance_backfill(conn, end_date=AS_OF, log=lambda m: None)
        check = run_performance_check(conn, as_of_date=AS_OF, log=lambda m: None)

    one_month = next(p for p in check.periods if p.period == "1m")
    assert one_month.status != GOAL_INSUFFICIENT
    assert one_month.investment_return_pct is not None
    # Prices only rose, so the one-month return must be positive.
    assert one_month.investment_return_pct > 0
