from __future__ import annotations

import duckdb

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository

# Market benchmarks. These are ETFs, not valuation targets: the equity-only
# factor/screening/valuation loops (asset_type="equity") skip them. They exist
# so price ingestion fetches their history, which the valuation layer regresses
# against for CAPM beta (SPY is the market proxy in compute_valuation.py).
SEED_BENCHMARKS = [
    Asset(
        asset_id="US_ETF_SPY",
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        asset_type="etf",
        country="US",
        exchange="NYSE Arca",
        currency="USD",
        source="manual_seed",
        metadata={"role": "benchmark"},
    ),
]


def seed_benchmarks(conn: duckdb.DuckDBPyConnection) -> None:
    AssetRepository(conn).upsert_many(SEED_BENCHMARKS)
