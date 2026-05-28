from __future__ import annotations

import duckdb

from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository

SEED_US_EQUITIES = [
    Asset(
        asset_id="US_EQ_AAPL",
        symbol="AAPL",
        name="Apple Inc.",
        asset_type="equity",
        country="US",
        exchange="NASDAQ",
        currency="USD",
        sector="Technology",
        industry="Consumer Electronics",
        source="manual_seed",
    ),
    Asset(
        asset_id="US_EQ_MSFT",
        symbol="MSFT",
        name="Microsoft Corporation",
        asset_type="equity",
        country="US",
        exchange="NASDAQ",
        currency="USD",
        sector="Technology",
        industry="Software",
        source="manual_seed",
    ),
    Asset(
        asset_id="US_EQ_NVDA",
        symbol="NVDA",
        name="NVIDIA Corporation",
        asset_type="equity",
        country="US",
        exchange="NASDAQ",
        currency="USD",
        sector="Technology",
        industry="Semiconductors",
        source="manual_seed",
    ),
]


def seed_us_equities(conn: duckdb.DuckDBPyConnection) -> None:
    AssetRepository(conn).upsert_many(SEED_US_EQUITIES)
