from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.models import (
    Exposure,
    Holding,
    PolicyDrift,
    Portfolio,
)
from croesus.portfolio.repository import PortfolioRepository

AS_OF = date(2026, 6, 1)


def _portfolio(**overrides) -> Portfolio:
    fields = dict(
        portfolio_id="default",
        profile_id="default",
        name="My portfolio",
        base_currency="USD",
    )
    fields.update(overrides)
    return Portfolio(**fields)


def test_migrate_creates_portfolio_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "portfolio.duckdb"

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

    assert {
        "portfolios",
        "portfolio_holdings",
        "portfolio_snapshots",
        "portfolio_exposures",
        "policy_drifts",
    } <= tables


def test_portfolio_repository_round_trips_portfolio(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    portfolio = _portfolio(metadata={"note": "test", "tags": ["a", "b"]})

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_portfolio(portfolio)
        loaded = repo.get_portfolio("default")

    assert loaded == portfolio


def test_portfolio_repository_get_missing_returns_none(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        assert PortfolioRepository(conn).get_portfolio("missing") is None


def test_portfolio_repository_upsert_preserves_created_at(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.upsert_portfolio(_portfolio())
        created_first = conn.execute(
            "SELECT created_at FROM portfolios WHERE portfolio_id = 'default'"
        ).fetchone()[0]
        repo.upsert_portfolio(_portfolio(name="renamed"))
        created_again, name = conn.execute(
            "SELECT created_at, name FROM portfolios WHERE portfolio_id = 'default'"
        ).fetchone()

    assert created_again == created_first
    assert name == "renamed"


def test_portfolio_repository_round_trips_holdings(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    holdings = [
        Holding("default", "US_EQ_AAPL", AS_OF, 10.0, 1900.0, "USD", cost_basis=1500.0),
        Holding("default", "CASH_USD", AS_OF, 1.0, 1000.0, "USD", cost_basis=1000.0),
    ]

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.replace_holdings("default", AS_OF, holdings)
        loaded = repo.get_holdings("default", AS_OF)

    assert {h.asset_id for h in loaded} == {"US_EQ_AAPL", "CASH_USD"}
    aapl = next(h for h in loaded if h.asset_id == "US_EQ_AAPL")
    assert aapl.market_value == 1900.0
    assert aapl.cost_basis == 1500.0
    assert aapl.as_of_date == AS_OF


def test_portfolio_repository_replace_holdings_removes_stale_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    first = [Holding("default", "US_EQ_AAPL", AS_OF, 10.0, 1900.0, "USD")]
    second = [Holding("default", "US_EQ_MSFT", AS_OF, 5.0, 2100.0, "USD")]

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.replace_holdings("default", AS_OF, first)
        repo.replace_holdings("default", AS_OF, second)
        loaded = repo.get_holdings("default", AS_OF)

    assert {h.asset_id for h in loaded} == {"US_EQ_MSFT"}


def test_portfolio_repository_round_trips_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.save_snapshot("default", AS_OF, 5000.0, cash_value=1000.0)
        snap = repo.get_snapshot("default", AS_OF)

    assert snap is not None
    assert snap["total_market_value"] == 5000.0
    assert snap["cash_value"] == 1000.0


def test_portfolio_repository_round_trips_exposures(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    exposures = [
        Exposure("default", AS_OF, "sector", "Technology", 0.8, 4000.0, 0.35, True),
        Exposure("default", AS_OF, "position", "US_EQ_AAPL", 0.38, 1900.0, 0.10, True),
    ]

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.replace_exposures("default", AS_OF, exposures)
        loaded = repo.get_exposures("default", AS_OF)

    assert len(loaded) == 2
    tech = next(e for e in loaded if e.exposure_type == "sector")
    assert tech.exposure_name == "Technology"
    assert tech.is_violation is True
    assert tech.limit_weight == 0.35


def test_portfolio_repository_round_trips_drifts(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    drifts = [
        PolicyDrift("default", AS_OF, "cash", 0.20, 0.10, 0.05, 0.20, 0.10, False),
        PolicyDrift("default", AS_OF, "core_us_equity", 0.80, 0.55, 0.45, 0.65, 0.25, True),
    ]

    with get_connection(db_path) as conn:
        repo = PortfolioRepository(conn)
        repo.replace_drifts("default", AS_OF, drifts)
        loaded = repo.get_drifts("default", AS_OF)

    assert {d.sleeve_name for d in loaded} == {"cash", "core_us_equity"}
    core = next(d for d in loaded if d.sleeve_name == "core_us_equity")
    assert core.drift == 0.25
    assert core.is_outside_band is True
