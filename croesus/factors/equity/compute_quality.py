"""
Compute and store quality factors (ROE, net margin, debt/equity).

Mirrors the valuation factor job: per active US equity, read the cached
fundamentals, derive the quality primitives, and upsert them into
``factor_values`` at ``as_of``. The screener percentile-ranks and blends them
into ``quality_score``. Equity-only and fundamental-driven — never used in the
backtest (look-ahead), only live screening + the forward-test.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.factors.common import FactorValue
from croesus.factors.equity.quality import compute_quality_metrics
from croesus.fundamentals.repository import (
    METRIC_NET_INCOME,
    METRIC_REVENUE,
    METRIC_TOTAL_DEBT,
    METRIC_TOTAL_EQUITY,
    FundamentalsRepository,
)


@dataclass(frozen=True)
class QualityComputationResult:
    computed: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)


def compute_and_store_quality_factors(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of: date | None = None,
    log: Callable[[str], None] = print,
) -> QualityComputationResult:
    as_of = as_of or date.today()
    assets = AssetRepository(conn).list_active(asset_type="equity", country="US")
    fundamentals = FundamentalsRepository(conn)
    result = QualityComputationResult()

    for asset in assets:
        metrics = compute_quality_metrics(
            net_income=fundamentals.get_latest_metric(asset.asset_id, METRIC_NET_INCOME),
            revenue=fundamentals.get_latest_metric(asset.asset_id, METRIC_REVENUE),
            total_equity=fundamentals.get_latest_metric(asset.asset_id, METRIC_TOTAL_EQUITY),
            total_debt=fundamentals.get_latest_metric(asset.asset_id, METRIC_TOTAL_DEBT),
        )
        if not metrics:
            result.skipped[asset.asset_id] = "no quality fundamentals"
            continue
        _upsert_factor_values(
            conn,
            [FactorValue(asset.asset_id, as_of, name, value) for name, value in metrics.items()],
        )
        result.computed[asset.asset_id] = len(metrics)
        log(f"stored {len(metrics)} quality factors for {asset.symbol}")

    return result


def _upsert_factor_values(
    conn: duckdb.DuckDBPyConnection, factor_values: list[FactorValue]
) -> None:
    conn.executemany(
        """
        INSERT INTO factor_values (asset_id, date, factor_name, value)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (asset_id, date, factor_name) DO UPDATE SET value = excluded.value
        """,
        [(f.asset_id, f.date, f.factor_name, f.value) for f in factor_values],
    )
