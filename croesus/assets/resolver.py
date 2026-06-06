from __future__ import annotations

from dataclasses import dataclass

import duckdb

from croesus.assets.metadata_provider import AssetMetadataProvider
from croesus.assets.models import Asset
from croesus.assets.repository import AssetRepository
from croesus.data_sources.base import DailyPriceSource
from croesus.prices.repository import PriceRepository


@dataclass(frozen=True)
class AssetResolution:
    symbol: str
    status: str
    asset: Asset | None = None
    message: str | None = None

    @property
    def asset_id(self) -> str | None:
        return self.asset.asset_id if self.asset else None


class AssetResolver:
    """Resolve user-facing identifiers into stable rows in the assets table."""

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        metadata_provider: AssetMetadataProvider | None = None,
        price_source: DailyPriceSource | None = None,
    ) -> None:
        self.conn = conn
        self.metadata_provider = metadata_provider
        self.price_source = price_source

    def resolve_symbol(self, symbol: str) -> AssetResolution:
        clean_symbol = symbol.strip().upper()
        if not clean_symbol:
            return AssetResolution(symbol=clean_symbol, status="unresolved", message="blank symbol")

        existing = self._find_by_symbol(clean_symbol)
        if existing is not None:
            return AssetResolution(symbol=clean_symbol, status="resolved", asset=existing)

        if self.metadata_provider is None:
            return AssetResolution(
                symbol=clean_symbol,
                status="unresolved",
                message="symbol not found in assets",
            )

        resolved = self.metadata_provider.get_asset(clean_symbol)
        if resolved is None:
            return AssetResolution(
                symbol=clean_symbol,
                status="unresolved",
                message="metadata provider could not resolve symbol",
            )

        AssetRepository(self.conn).upsert_many([resolved])
        message = self._bootstrap_prices(resolved)
        return AssetResolution(
            symbol=clean_symbol,
            status="created",
            asset=resolved,
            message=message,
        )

    def _find_by_symbol(self, symbol: str) -> Asset | None:
        row = self.conn.execute(
            """
            SELECT * FROM assets
            WHERE upper(symbol) = ? AND is_active = TRUE
            ORDER BY asset_id
            LIMIT 1
            """,
            [symbol.upper()],
        ).fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in self.conn.description]
        return AssetRepository._row_to_asset(dict(zip(columns, row)))

    def _bootstrap_prices(self, asset: Asset) -> str | None:
        if self.price_source is None:
            return None
        try:
            prices = self.price_source.fetch_daily_prices(asset.symbol, period="1y")
            rows = PriceRepository(self.conn).upsert_daily_prices(
                asset.asset_id,
                prices,
                source=getattr(self.price_source, "source_name", "asset_resolver"),
            )
        except Exception as exc:  # pragma: no cover - exercised through integration logs.
            return f"price bootstrap failed: {exc}"
        return f"price bootstrap stored {rows} rows"
