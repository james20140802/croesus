from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import duckdb
import pandas as pd

from croesus.assets.classifier import PRICEABLE_ASSET_TYPES
from croesus.assets.repository import AssetRepository
from croesus.factors.common import FactorValue, compute_common_factors
from croesus.prices.repository import PriceRepository

# Market proxy for beta (kept in sync with the valuation pipeline's benchmark).
_BENCHMARK_SYMBOL = "SPY"


def _market_returns(
    conn: duckdb.DuckDBPyConnection, prices: PriceRepository
) -> dict[date, float]:
    """Daily benchmark returns by date, for the beta factor."""
    row = conn.execute(
        "SELECT asset_id FROM assets WHERE symbol = ? LIMIT 1", [_BENCHMARK_SYMBOL]
    ).fetchone()
    if row is None:
        return {}
    frame = prices.load_daily_prices(row[0]).sort_values("date")
    closes = [(d, c) for d, c in zip(frame["date"], frame["close"]) if c is not None]
    out: dict[date, float] = {}
    for (_, prev), (cur_date, cur) in zip(closes, closes[1:]):
        if prev:
            out[pd.Timestamp(cur_date).date()] = float(cur) / float(prev) - 1.0
    return out


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
    market_returns = _market_returns(conn, prices)

    for asset in assets:
        try:
            frame = prices.load_daily_prices(asset.asset_id)
            factor_values = compute_common_factors(
                asset.asset_id, frame, market_returns=market_returns
            )
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
