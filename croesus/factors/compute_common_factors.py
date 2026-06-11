from __future__ import annotations

from dataclasses import dataclass, field

import duckdb

from croesus.assets.classifier import PRICEABLE_ASSET_TYPES
from croesus.assets.repository import AssetRepository
from croesus.factors.common import FactorValue, compute_common_factors
from croesus.prices.repository import PriceRepository


@dataclass(frozen=True)
class FactorComputationResult:
    computed: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def compute_and_store_common_factors(conn: duckdb.DuckDBPyConnection) -> FactorComputationResult:
    # Price-derived factors (momentum, volatility, liquidity, 200d MA) are
    # asset-type-agnostic: any asset with a daily close series gets them.
    # Valuation/DCF stays equity-only — that filter lives in compute_valuation.
    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in PRICEABLE_ASSET_TYPES
    ]
    prices = PriceRepository(conn)
    result = FactorComputationResult()

    for asset in assets:
        try:
            frame = prices.load_daily_prices(asset.asset_id)
            factor_values = compute_common_factors(asset.asset_id, frame)
            if not factor_values:
                result.skipped[asset.asset_id] = "insufficient price history"
                print(f"skip factors for {asset.symbol}: insufficient price history")
                continue
            _upsert_factor_values(conn, factor_values)
            result.computed[asset.asset_id] = len(factor_values)
            print(f"stored {len(factor_values)} common factors for {asset.symbol}")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the run.
            result.failed[asset.asset_id] = str(exc)
            print(f"failed factors for {asset.symbol}: {exc}")

    return result


def _upsert_factor_values(
    conn: duckdb.DuckDBPyConnection,
    factor_values: list[FactorValue],
) -> None:
    conn.executemany(
        """
        INSERT INTO factor_values (asset_id, date, factor_name, value)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (asset_id, date, factor_name) DO UPDATE SET
          value = excluded.value
        """,
        [
            (factor.asset_id, factor.date, factor.factor_name, factor.value)
            for factor in factor_values
        ],
    )
