from __future__ import annotations

import json
from typing import Any

import duckdb

from croesus.assets.models import Asset


class AssetRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_many(self, assets: list[Asset]) -> None:
        if not assets:
            return
        self.conn.executemany(
            """
            INSERT INTO assets (
              asset_id, symbol, name, asset_type, country, exchange, currency,
              sector, industry, is_active, source, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON)
            ON CONFLICT (asset_id) DO UPDATE SET
              symbol = excluded.symbol,
              name = excluded.name,
              asset_type = excluded.asset_type,
              country = excluded.country,
              exchange = excluded.exchange,
              currency = excluded.currency,
              sector = excluded.sector,
              industry = excluded.industry,
              is_active = excluded.is_active,
              source = excluded.source,
              metadata = excluded.metadata
            """,
            [self._asset_to_row(asset) for asset in assets],
        )

    def list_active(
        self,
        *,
        asset_type: str | None = None,
        country: str | None = None,
    ) -> list[Asset]:
        sql = "SELECT * FROM assets WHERE is_active = TRUE"
        params: list[Any] = []
        if asset_type is not None:
            sql += " AND asset_type = ?"
            params.append(asset_type)
        if country is not None:
            sql += " AND country = ?"
            params.append(country)
        sql += " ORDER BY asset_id"
        rows = self.conn.execute(sql, params).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [self._row_to_asset(dict(zip(columns, row))) for row in rows]

    @staticmethod
    def _asset_to_row(asset: Asset) -> tuple[Any, ...]:
        return (
            asset.asset_id,
            asset.symbol,
            asset.name,
            asset.asset_type,
            asset.country,
            asset.exchange,
            asset.currency,
            asset.sector,
            asset.industry,
            asset.is_active,
            asset.source,
            json.dumps(asset.metadata),
        )

    @staticmethod
    def _row_to_asset(row: dict[str, Any]) -> Asset:
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return Asset(
            asset_id=row["asset_id"],
            symbol=row["symbol"],
            name=row["name"],
            asset_type=row["asset_type"],
            country=row["country"],
            exchange=row["exchange"],
            currency=row["currency"],
            sector=row["sector"],
            industry=row["industry"],
            is_active=bool(row["is_active"]),
            source=row["source"],
            metadata=metadata,
        )
