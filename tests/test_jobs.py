from pathlib import Path

from croesus.assets.repository import AssetRepository
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.bootstrap import main as bootstrap_main
from croesus.jobs.daily_run import run_daily_pipeline


def test_bootstrap_job_uses_configured_db_path_and_seeds_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "configured.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    bootstrap_main()

    with get_connection(db_path) as conn:
        symbols = [
            row[0]
            for row in conn.execute("SELECT symbol FROM assets ORDER BY asset_id").fetchall()
        ]

    # AAPL/MSFT/NVDA seeded as equities; SPY seeded as the ETF market benchmark
    # (US_ETF_SPY sorts after the US_EQ_* ids).
    assert symbols == ["AAPL", "MSFT", "NVDA", "SPY"]


class EmptyPriceSource:
    def fetch_daily_prices(self, symbol: str, period: str = "1y"):
        import pandas as pd

        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "adjusted_close", "volume"]
        )


def test_daily_pipeline_seeds_assets_before_price_ingestion(tmp_path: Path) -> None:
    db_path = tmp_path / "daily.duckdb"
    migrate(db_path)

    with get_connection(db_path) as conn:
        result = run_daily_pipeline(conn, source=EmptyPriceSource(), log=lambda message: None)
        assets = AssetRepository(conn).list_active(asset_type="equity", country="US")

    # The equity valuation universe excludes the SPY benchmark (it is an ETF)...
    assert [asset.symbol for asset in assets] == ["AAPL", "MSFT", "NVDA"]
    # ...but price ingestion fetches SPY too, so it is skipped by the empty source.
    assert result.price_result.skipped == {
        "AAPL": "no price rows returned",
        "MSFT": "no price rows returned",
        "NVDA": "no price rows returned",
        "SPY": "no price rows returned",
    }
