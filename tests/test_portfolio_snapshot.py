from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.import_holdings import load_holdings_csv
from croesus.portfolio.models import (
    Exposure,
    Holding,
    PolicyDrift,
    Portfolio,
)
from croesus.portfolio.repository import PortfolioRepository

AS_OF = date(2026, 6, 1)


def _seed_assets(conn: duckdb.DuckDBPyConnection) -> None:
    AssetRepository(conn).upsert_many(
        [
            Asset(
                asset_id="US_EQ_AAPL",
                symbol="AAPL",
                name="Apple Inc.",
                asset_type="equity",
                country="US",
                currency="USD",
                sector="Technology",
                industry="Consumer Electronics",
            ),
            Asset(
                asset_id="US_EQ_MSFT",
                symbol="MSFT",
                name="Microsoft Corporation",
                asset_type="equity",
                country="US",
                currency="USD",
                sector="Technology",
                industry="Software",
            ),
        ]
    )


def _write_csv(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _seed_snapshot_assets(conn: duckdb.DuckDBPyConnection) -> None:
    """Assets whose types/themes exercise every default sleeve and theme exposure."""
    AssetRepository(conn).upsert_many(
        [
            Asset(
                asset_id="US_EQ_AAPL", symbol="AAPL", name="Apple Inc.",
                asset_type="equity", country="US", currency="USD",
                sector="Technology", industry="Consumer Electronics",
            ),
            Asset(
                asset_id="ETF_VOO", symbol="VOO", name="Vanguard S&P 500 ETF",
                asset_type="etf", country="US", currency="USD",
                sector="Diversified", industry="Index Fund",
                metadata={"theme_tags": ["broad_market"]},
            ),
            Asset(
                asset_id="ETF_BND", symbol="BND", name="Vanguard Total Bond ETF",
                asset_type="bond_etf", country="US", currency="USD",
                sector="Fixed Income", industry="Bond Fund",
            ),
        ]
    )


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


# -- Task 3: holdings CSV import ------------------------------------------------


def test_load_holdings_csv_imports_valid_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency,cost_basis\n"
        "default,US_EQ_AAPL,10,1900,USD,1500\n"
        "default,US_EQ_MSFT,5,2100,USD,1800\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.skipped == 0
    assert {h.asset_id for h in result.holdings} == {"US_EQ_AAPL", "US_EQ_MSFT"}
    aapl = next(h for h in result.holdings if h.asset_id == "US_EQ_AAPL")
    assert aapl.market_value == 1900.0
    assert aapl.cost_basis == 1500.0
    assert aapl.as_of_date == AS_OF


def test_load_holdings_csv_defaults_portfolio_id(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "asset_id,quantity,market_value,currency\nUS_EQ_AAPL,10,1900,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.holdings[0].portfolio_id == "default"


def test_load_holdings_csv_resolves_existing_asset_by_symbol(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "symbol,quantity,market_value,currency\nAAPL,10,1900,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.skipped == 0
    assert [h.asset_id for h in result.holdings] == ["US_EQ_AAPL"]
    assert result.resolver_statuses[0].status == "resolved"
    assert result.resolver_statuses[0].symbol == "AAPL"
    assert result.resolver_statuses[0].asset_id == "US_EQ_AAPL"


def test_load_holdings_csv_creates_resolvable_symbol_asset(tmp_path: Path) -> None:
    class StaticMetadataProvider:
        def get_asset(self, symbol: str) -> Asset | None:
            if symbol == "VOO":
                return Asset(
                    asset_id="US_ETF_VOO",
                    symbol="VOO",
                    name="Vanguard S&P 500 ETF",
                    asset_type="etf",
                    country="US",
                    exchange="NYSEARCA",
                    currency="USD",
                    sector="Diversified",
                    industry="Index Fund",
                    source="test_metadata",
                    metadata={"theme_tags": ["broad_market"]},
                )
            return None

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "symbol,quantity,avg_cost,currency\nVOO,5,430,USD\n",
    )

    with get_connection(db_path) as conn:
        result = load_holdings_csv(
            csv_path,
            conn,
            AS_OF,
            metadata_provider=StaticMetadataProvider(),
        )
        assets = AssetRepository(conn).list_active(asset_type="etf", country="US")

    assert result.skipped == 0
    assert [h.asset_id for h in result.holdings] == ["US_ETF_VOO"]
    assert result.resolver_statuses[0].status == "created"
    assert result.resolver_statuses[0].symbol == "VOO"
    assert [asset.asset_id for asset in assets] == ["US_ETF_VOO"]
    assert assets[0].metadata["theme_tags"] == ["broad_market"]


def test_load_holdings_csv_reports_unresolved_symbol_status(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "symbol,quantity,market_value,currency\nGHOST,1,500,USD\n",
    )

    with get_connection(db_path) as conn:
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.holdings == []
    assert result.skipped == 1
    assert result.resolver_statuses[0].status == "unresolved"
    assert result.resolver_statuses[0].symbol == "GHOST"
    assert any("unresolved symbol GHOST" in warning for warning in result.warnings)


def test_load_holdings_csv_warns_when_asset_id_symbol_mismatch(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "asset_id,symbol,quantity,market_value,currency\nUS_EQ_MSFT,AAPL,5,2100,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.skipped == 0
    assert [h.asset_id for h in result.holdings] == ["US_EQ_MSFT"]
    assert result.resolver_statuses[0].status == "skipped"
    assert result.resolver_statuses[0].asset_id == "US_EQ_MSFT"
    assert result.resolver_statuses[0].symbol == "AAPL"
    assert any("symbol AAPL does not match asset US_EQ_MSFT symbol MSFT" in w for w in result.warnings)


def test_load_holdings_csv_bootstraps_prices_for_created_symbol_asset(
    tmp_path: Path,
) -> None:
    from croesus.prices.repository import PriceRepository

    class StaticMetadataProvider:
        def get_asset(self, symbol: str) -> Asset | None:
            if symbol == "VOO":
                return Asset(
                    asset_id="US_ETF_VOO",
                    symbol="VOO",
                    name="Vanguard S&P 500 ETF",
                    asset_type="etf",
                    country="US",
                    exchange="NYSEARCA",
                    currency="USD",
                    sector="Diversified",
                    industry="Index Fund",
                    source="test_metadata",
                )
            return None

    class StaticPriceSource:
        def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
            assert symbol == "VOO"
            return pd.DataFrame(
                [
                    {
                        "date": AS_OF,
                        "open": 430.0,
                        "high": 432.0,
                        "low": 429.0,
                        "close": 431.0,
                        "adjusted_close": 431.0,
                        "volume": 1000,
                    }
                ]
            )

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "symbol,quantity,avg_cost,currency\nVOO,5,430,USD\n",
    )

    with get_connection(db_path) as conn:
        result = load_holdings_csv(
            csv_path,
            conn,
            AS_OF,
            metadata_provider=StaticMetadataProvider(),
            price_source=StaticPriceSource(),
        )
        latest_close = PriceRepository(conn).get_latest_close("US_ETF_VOO", AS_OF)

    assert result.skipped == 0
    assert result.resolver_statuses[0].status == "created"
    assert latest_close == 431.0


def test_load_holdings_csv_keeps_created_asset_when_price_bootstrap_fails(
    tmp_path: Path,
) -> None:
    class StaticMetadataProvider:
        def get_asset(self, symbol: str) -> Asset | None:
            return Asset(
                asset_id="US_ETF_VOO",
                symbol=symbol,
                name="Vanguard S&P 500 ETF",
                asset_type="etf",
                country="US",
                exchange="NYSEARCA",
                currency="USD",
                sector="Diversified",
                industry="Index Fund",
                source="test_metadata",
            )

    class FailingPriceSource:
        def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
            raise RuntimeError("temporary price outage")

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "symbol,quantity,avg_cost,currency\nVOO,5,430,USD\n",
    )

    with get_connection(db_path) as conn:
        result = load_holdings_csv(
            csv_path,
            conn,
            AS_OF,
            metadata_provider=StaticMetadataProvider(),
            price_source=FailingPriceSource(),
        )
        assets = AssetRepository(conn).list_active(asset_type="etf", country="US")

    assert result.skipped == 0
    assert [h.asset_id for h in result.holdings] == ["US_ETF_VOO"]
    assert [asset.asset_id for asset in assets] == ["US_ETF_VOO"]
    assert result.resolver_statuses[0].status == "created"
    assert "price bootstrap failed" in (result.resolver_statuses[0].message or "")
    assert any("price bootstrap failed" in warning for warning in result.warnings)


def test_load_holdings_csv_accepts_cash_row(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    # CASH_USD is not in the assets table, but must be accepted (not skipped).
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency\n"
        "default,CASH_USD,1,1000,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.skipped == 0
    assert [h.asset_id for h in result.holdings] == ["CASH_USD"]


def test_load_holdings_csv_accepts_multi_currency_cash_row(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency\n"
        "default,CASH_KRW,,421391,KRW\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.skipped == 0
    assert [h.asset_id for h in result.holdings] == ["CASH_KRW"]
    assert result.holdings[0].market_value == 421391.0
    assert result.holdings[0].currency == "KRW"


def test_load_holdings_csv_skips_unknown_asset_with_warning(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency\n"
        "default,US_EQ_AAPL,10,1900,USD\n"
        "default,US_EQ_GHOST,1,500,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert [h.asset_id for h in result.holdings] == ["US_EQ_AAPL"]
    assert result.skipped == 1
    assert any("US_EQ_GHOST" in w for w in result.warnings)


def test_load_holdings_csv_defaults_currency_to_profile_base(tmp_path: Path) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value\ndefault,US_EQ_AAPL,10,1900\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        # base currency comes from the active profile, not the trivial USD fallback
        conn.execute(
            "INSERT INTO investor_profiles (profile_id, base_currency) VALUES (?, ?)",
            ["default", "KRW"],
        )
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.holdings[0].currency == "KRW"


def test_load_holdings_csv_accepts_quantity_and_avg_cost_without_market_value(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,avg_cost,market_value,currency\n"
        "default,US_EQ_AAPL,10,150,,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.skipped == 0
    assert result.holdings[0].quantity == 10.0
    assert result.holdings[0].avg_cost == 150.0
    assert result.holdings[0].market_value is None


def test_load_holdings_csv_skips_non_cash_row_without_valuation_inputs(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,avg_cost,market_value,currency\n"
        "default,US_EQ_AAPL,,,,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF)

    assert result.holdings == []
    assert result.skipped == 1
    assert any("quantity/avg_cost or market_value" in w for w in result.warnings)


def test_default_policy_targets_carry_sleeve_mapping_metadata() -> None:
    # the default seed must carry sleeve->holding mapping metadata, or drift
    # would be uncomputable for the out-of-the-box profile.
    from croesus.profiles.seed_default_profile import DEFAULT_POLICY_TARGETS

    by_name = {t.sleeve_name: t for t in DEFAULT_POLICY_TARGETS}
    assert by_name["cash"].metadata.get("asset_ids") == ["CASH_USD"]
    assert "equity" in by_name["satellite_equity"].metadata.get("asset_types", [])
    assert "etf" in by_name["core_us_equity"].metadata.get("asset_types", [])
    assert "bond_etf" in by_name["defensive_bonds"].metadata.get("asset_types", [])


# -- Task 5: portfolio_snapshot job --------------------------------------------


def test_run_portfolio_snapshot_writes_all_tables(tmp_path: Path) -> None:
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.seed_default_profile import seed_default_profile

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency,cost_basis\n"
        "default,US_EQ_AAPL,10,1900,USD,1500\n"
        "default,ETF_VOO,5,2100,USD,1800\n"
        "default,ETF_BND,3,1000,USD,1000\n"
        "default,CASH_USD,1,1000,USD,1000\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        _seed_snapshot_assets(conn)
        result = run_portfolio_snapshot(
            conn, csv_path, portfolio_id="default", as_of_date=AS_OF, log=lambda m: None
        )

        repo = PortfolioRepository(conn)
        snap = repo.get_snapshot("default", AS_OF)
        exposures = repo.get_exposures("default", AS_OF)
        drifts = repo.get_drifts("default", AS_OF)

    # result summary
    assert result.holdings_imported == 4
    assert result.holdings_skipped == 0
    assert result.total_market_value == 6000.0

    # portfolio_snapshots
    assert snap is not None
    assert snap["total_market_value"] == 6000.0
    assert snap["cash_value"] == 1000.0

    # portfolio_exposures: position weights sum to 1, theme tag was read
    positions = [e for e in exposures if e.exposure_type == "position"]
    assert abs(sum(e.weight for e in positions) - 1.0) < 1e-9
    themes = {e.exposure_name for e in exposures if e.exposure_type == "theme"}
    assert "broad_market" in themes

    # policy_drifts: one row per default sleeve, each holding mapped cleanly
    assert {d.sleeve_name for d in drifts} == {
        "core_us_equity", "satellite_equity", "defensive_bonds", "cash"
    }
    sat = next(d for d in drifts if d.sleeve_name == "satellite_equity")
    assert abs(sat.current_weight - (1900.0 / 6000.0)) < 1e-9
    assert len(result.warnings) == 3
    assert all("PRICE_MISSING" in warning for warning in result.warnings)


def test_run_portfolio_snapshot_resolves_symbol_and_reports_statuses(
    tmp_path: Path,
) -> None:
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.seed_default_profile import seed_default_profile

    class StaticMetadataProvider:
        def get_asset(self, symbol: str) -> Asset | None:
            if symbol == "VOO":
                return Asset(
                    asset_id="US_ETF_VOO",
                    symbol="VOO",
                    name="Vanguard S&P 500 ETF",
                    asset_type="etf",
                    country="US",
                    exchange="NYSEARCA",
                    currency="USD",
                    sector="Diversified",
                    industry="Index Fund",
                    source="test_metadata",
                    metadata={"theme_tags": ["broad_market"]},
                )
            return None

    class StaticPriceSource:
        def fetch_daily_prices(self, symbol: str, period: str = "1y") -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "date": AS_OF,
                        "open": 430.0,
                        "high": 432.0,
                        "low": 429.0,
                        "close": 431.0,
                        "adjusted_close": 431.0,
                        "volume": 1000,
                    }
                ]
            )

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,symbol,quantity,avg_cost,currency\n"
        "default,VOO,5,430,USD\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        result = run_portfolio_snapshot(
            conn,
            csv_path,
            portfolio_id="default",
            as_of_date=AS_OF,
            metadata_provider=StaticMetadataProvider(),
            price_source=StaticPriceSource(),
            log=lambda m: None,
        )
        holdings = PortfolioRepository(conn).get_holdings("default", AS_OF)

    assert result.holdings_imported == 1
    assert result.holdings_skipped == 0
    assert result.total_market_value == 2155.0
    assert result.resolver_statuses[0].status == "created"
    assert result.resolver_statuses[0].symbol == "VOO"
    assert result.resolver_statuses[0].asset_id == "US_ETF_VOO"
    assert [h.asset_id for h in holdings] == ["US_ETF_VOO"]


def test_run_portfolio_snapshot_marks_to_market_and_persists_pnl(
    tmp_path: Path,
) -> None:
    from croesus.fx.repository import FxRepository
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.prices.repository import PriceRepository
    from croesus.profiles.seed_default_profile import seed_default_profile

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,avg_cost,currency,market_value\n"
        "default,US_EQ_AAPL,10,150,USD,\n"
        "default,CASH_KRW,,,KRW,150000\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        _seed_snapshot_assets(conn)
        PriceRepository(conn).upsert_daily_prices(
            "US_EQ_AAPL",
            pd.DataFrame(
                [
                    {
                        "date": AS_OF,
                        "open": 190.0,
                        "high": 191.0,
                        "low": 189.0,
                        "close": 190.0,
                        "adjusted_close": 190.0,
                        "volume": 1000,
                    }
                ]
            ),
            source="test",
        )
        FxRepository(conn).upsert_rates(
            "KRW",
            pd.DataFrame(
                [{"date": AS_OF, "rate_per_usd": 1500.0}]
            ),
            source="test",
        )

        result = run_portfolio_snapshot(
            conn, csv_path, portfolio_id="default", as_of_date=AS_OF, log=lambda m: None
        )
        repo = PortfolioRepository(conn)
        snap = repo.get_snapshot("default", AS_OF)
        holdings = repo.get_holdings("default", AS_OF)

    assert result.total_market_value == 2000.0
    assert result.total_cost_basis == 1600.0
    assert result.unrealized_pnl == 400.0
    assert snap is not None
    assert snap["total_cost_basis"] == 1600.0
    assert snap["unrealized_pnl"] == 400.0

    by_asset = {h.asset_id: h for h in holdings}
    assert by_asset["US_EQ_AAPL"].market_value == 1900.0
    assert by_asset["US_EQ_AAPL"].cost_basis == 1500.0
    assert by_asset["US_EQ_AAPL"].metadata["price_source"] == "store"
    assert by_asset["CASH_KRW"].market_value == 100.0
    assert by_asset["CASH_KRW"].cost_basis == 100.0


def test_run_portfolio_snapshot_persists_unknown_pnl_when_cost_basis_is_unknown(
    tmp_path: Path,
) -> None:
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.seed_default_profile import seed_default_profile

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency\n"
        "default,US_EQ_AAPL,,1800,USD\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        _seed_snapshot_assets(conn)

        result = run_portfolio_snapshot(
            conn, csv_path, portfolio_id="default", as_of_date=AS_OF, log=lambda m: None
        )
        snap = PortfolioRepository(conn).get_snapshot("default", AS_OF)

    assert result.total_market_value == 1800.0
    assert result.total_cost_basis is None
    assert result.unrealized_pnl is None
    assert snap is not None
    assert snap["total_market_value"] == 1800.0
    assert snap["total_cost_basis"] is None
    assert snap["unrealized_pnl"] is None


def test_run_portfolio_snapshot_survives_unknown_asset(tmp_path: Path) -> None:
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.seed_default_profile import seed_default_profile

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency\n"
        "default,US_EQ_AAPL,10,1900,USD\n"
        "default,US_EQ_GHOST,1,500,USD\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        _seed_snapshot_assets(conn)
        result = run_portfolio_snapshot(
            conn, csv_path, portfolio_id="default", as_of_date=AS_OF, log=lambda m: None
        )
        snap = PortfolioRepository(conn).get_snapshot("default", AS_OF)

    # unknown asset is skipped, run still completes and persists a snapshot
    assert result.holdings_imported == 1
    assert result.holdings_skipped == 1
    assert any("US_EQ_GHOST" in w for w in result.warnings)
    assert snap is not None
    assert snap["total_market_value"] == 1900.0


def test_run_portfolio_snapshot_creates_portfolio_row(tmp_path: Path) -> None:
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.seed_default_profile import seed_default_profile

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency\ndefault,US_EQ_AAPL,10,1900,USD\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        _seed_snapshot_assets(conn)
        run_portfolio_snapshot(
            conn, csv_path, portfolio_id="default", as_of_date=AS_OF, log=lambda m: None
        )
        portfolio = PortfolioRepository(conn).get_portfolio("default")

    assert portfolio is not None
    assert portfolio.profile_id == "default"


# -- PR review fixes: portfolio/profile context for the holdings import --------


def test_load_holdings_csv_uses_given_portfolio_id_as_default(tmp_path: Path) -> None:
    # rows omitting portfolio_id adopt the caller's target, not a hardcoded "default"
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "asset_id,quantity,market_value,currency\nUS_EQ_AAPL,10,1900,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF, portfolio_id="ira")

    assert result.holdings[0].portfolio_id == "ira"


def test_load_holdings_csv_uses_explicit_base_currency(tmp_path: Path) -> None:
    # the caller's base currency wins over the DB default-profile lookup
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "asset_id,quantity,market_value\nUS_EQ_AAPL,10,1900\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        # a USD default profile exists, but the caller-supplied KRW must win
        conn.execute(
            "INSERT INTO investor_profiles (profile_id, base_currency) VALUES (?, ?)",
            ["default", "USD"],
        )
        result = load_holdings_csv(csv_path, conn, AS_OF, base_currency="KRW")

    assert result.holdings[0].currency == "KRW"


def test_load_holdings_csv_skips_and_counts_other_portfolio_rows(tmp_path: Path) -> None:
    # rows explicitly naming a different portfolio are skipped + counted (not silent)
    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value,currency\n"
        "ira,US_EQ_AAPL,10,1900,USD\n"
        "other,US_EQ_MSFT,5,2100,USD\n",
    )

    with get_connection(db_path) as conn:
        _seed_assets(conn)
        result = load_holdings_csv(csv_path, conn, AS_OF, portfolio_id="ira")

    assert [h.asset_id for h in result.holdings] == ["US_EQ_AAPL"]
    assert result.skipped == 1
    assert any("other" in w for w in result.warnings)


def test_run_portfolio_snapshot_defaults_currency_to_portfolio_profile(tmp_path: Path) -> None:
    # 'ira' is linked to a KRW profile; the default profile is USD. A CSV that
    # omits currency must store KRW (the governing profile), not USD.
    from dataclasses import replace

    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.models import Currency
    from croesus.profiles.repository import ProfileRepository
    from croesus.profiles.seed_default_profile import DEFAULT_PROFILE, seed_default_profile

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "portfolio_id,asset_id,quantity,market_value\nira,US_EQ_AAPL,10,1900\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)  # default profile = USD
        krw_profile = replace(
            DEFAULT_PROFILE, profile_id="retire_krw", base_currency=Currency.KRW
        )
        ProfileRepository(conn).upsert_profile(krw_profile)
        PortfolioRepository(conn).upsert_portfolio(
            Portfolio("ira", "retire_krw", "IRA", "KRW")
        )
        _seed_snapshot_assets(conn)

        run_portfolio_snapshot(
            conn, csv_path, portfolio_id="ira", as_of_date=AS_OF, log=lambda m: None
        )
        holdings = PortfolioRepository(conn).get_holdings("ira", AS_OF)

    assert holdings[0].currency == "KRW"


def test_run_portfolio_snapshot_imports_rows_for_target_portfolio_without_column(
    tmp_path: Path,
) -> None:
    # --portfolio-id ira + CSV without a portfolio_id column must import, not drop
    from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot
    from croesus.profiles.seed_default_profile import seed_default_profile

    db_path = tmp_path / "p.duckdb"
    migrate(db_path)
    csv_path = _write_csv(
        tmp_path / "h.csv",
        "asset_id,quantity,market_value,currency\nUS_EQ_AAPL,10,1900,USD\n",
    )

    with get_connection(db_path) as conn:
        seed_default_profile(conn)
        _seed_snapshot_assets(conn)
        result = run_portfolio_snapshot(
            conn, csv_path, portfolio_id="ira", as_of_date=AS_OF, log=lambda m: None
        )
        holdings = PortfolioRepository(conn).get_holdings("ira", AS_OF)

    assert result.holdings_imported == 1
    assert result.total_market_value == 1900.0
    assert [h.asset_id for h in holdings] == ["US_EQ_AAPL"]
