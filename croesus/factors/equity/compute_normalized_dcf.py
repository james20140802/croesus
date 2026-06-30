"""Orchestration for the normalized-FCF reverse-DCF methodology.

Reads prices + cached fundamentals, REUSES the WACC the mechanical valuation
run already persisted (so beta is not recomputed), calls the pure math in
:mod:`croesus.factors.equity.normalized`, and writes one row per asset to
``normalized_dcf_snapshots``. Per-asset failures are logged and skipped — the
existing mechanical DCF and the rest of the run are never disturbed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.factors.equity.normalized import evaluate_normalized_dcf
from croesus.factors.equity.normalized_repository import (
    NormalizedDcfRepository,
    NormalizedDcfSnapshot,
)
from croesus.factors.equity.repository import ValuationSnapshotRepository
from croesus.fundamentals.repository import (
    METRIC_CASH_AND_EQUIVALENTS,
    METRIC_SHARES_OUTSTANDING,
    METRIC_TOTAL_DEBT,
    FundamentalsRepository,
)
from croesus.prices.repository import PriceRepository


@dataclass(frozen=True)
class NormalizedDcfComputationResult:
    computed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def compute_and_store_normalized_dcf(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of: date | None = None,
    log: Callable[[str], None] = print,
) -> NormalizedDcfComputationResult:
    """Compute normalized DCF snapshots for all active US equities.

    WACC is reused from the mechanical ``valuation_snapshots`` row for ``as_of``.
    Assets without a mechanical snapshot are skipped (reason: "no mechanical wacc").
    Per-asset exceptions are caught so one bad asset never stops the run.
    """
    as_of = as_of or date.today()
    assets = AssetRepository(conn).list_active(asset_type="equity", country="US")
    prices = PriceRepository(conn)
    fundamentals = FundamentalsRepository(conn)
    valuation_repo = ValuationSnapshotRepository(conn)
    out_repo = NormalizedDcfRepository(conn)
    result = NormalizedDcfComputationResult()

    for asset in assets:
        try:
            price = prices.get_latest_close(asset.asset_id, as_of)
            if price is None:
                result.skipped[asset.asset_id] = "no price"
                log(f"skip normalized DCF for {asset.symbol}: no price on or before {as_of}")
                continue

            snapshot = valuation_repo.get(asset.asset_id, as_of)
            if snapshot is None or snapshot.wacc is None:
                result.skipped[asset.asset_id] = "no mechanical wacc"
                log(f"skip normalized DCF for {asset.symbol}: no mechanical wacc for {as_of}")
                continue

            annual_fcf = [v for _, v in fundamentals.get_annual_fcf(asset.asset_id)]
            if not annual_fcf:
                result.skipped[asset.asset_id] = "no FCF history"
                log(f"skip normalized DCF for {asset.symbol}: no FCF history")
                continue

            shares = fundamentals.get_latest_metric(asset.asset_id, METRIC_SHARES_OUTSTANDING)
            if not shares:
                result.skipped[asset.asset_id] = "no shares outstanding"
                log(f"skip normalized DCF for {asset.symbol}: no shares outstanding")
                continue

            total_debt = fundamentals.get_latest_metric(asset.asset_id, METRIC_TOTAL_DEBT)
            cash = fundamentals.get_latest_metric(asset.asset_id, METRIC_CASH_AND_EQUIVALENTS)

            evaluation = evaluate_normalized_dcf(
                annual_fcf=annual_fcf,
                price=price,
                wacc=snapshot.wacc,
                shares_outstanding=shares,
                total_debt=total_debt,
                cash=cash,
            )
            out_repo.upsert(NormalizedDcfSnapshot(
                asset_id=asset.asset_id,
                date=as_of,
                current_price=price,
                normalized_base_fcf=evaluation.normalized_base_fcf,
                reference_growth=evaluation.reference_growth,
                normalized_intrinsic_value_per_share=(
                    evaluation.normalized_intrinsic_value_per_share),
                normalized_upside_pct=evaluation.normalized_upside_pct,
                implied_growth=evaluation.implied_growth,
                plausibility_gap=evaluation.plausibility_gap,
                valuation_quality=evaluation.valuation_quality,
                n_fcf_years=evaluation.n_fcf_years,
                wacc=snapshot.wacc,
                assumptions={
                    "source": "model",
                    "window": evaluation.n_fcf_years,
                    "total_debt": total_debt,
                    "cash": cash,
                    "shares": shares,
                },
            ))
            result.computed.append(asset.asset_id)
            log(
                f"normalized DCF stored for {asset.symbol}: "
                f"quality={evaluation.valuation_quality} "
                f"gap={evaluation.plausibility_gap}"
            )
        except Exception as exc:  # noqa: BLE001 - per-asset isolation
            result.failed[asset.asset_id] = str(exc)
            log(f"normalized DCF failed for {asset.symbol}: {exc}")

    return result
