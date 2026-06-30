from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.equity.compute_normalized_dcf import (
    compute_and_store_normalized_dcf,
)
from croesus.factors.equity.normalized_repository import NormalizedDcfRepository
from croesus.factors.equity.repository import (
    ValuationSnapshot,
    ValuationSnapshotRepository,
)
from croesus.fundamentals.repository import (
    METRIC_CASH_AND_EQUIVALENTS,
    METRIC_FREE_CASH_FLOW,
    METRIC_SHARES_OUTSTANDING,
    METRIC_TOTAL_DEBT,
    FundamentalMetric,
    FundamentalsRepository,
)
from croesus.prices.repository import PriceRepository


def _seed_asset(conn, asset_id="US_EQ_AAPL"):
    # 5 years of ~flat positive FCF, shares/debt/cash, a price, and a mechanical wacc.
    fr = FundamentalsRepository(conn)
    metrics = []
    for i, y in enumerate(range(2021, 2026)):
        metrics.append(FundamentalMetric(asset_id, date(y, 9, 30), "annual",
                                         METRIC_FREE_CASH_FLOW, 100.0e9 + i, "test"))
    for name, val in [(METRIC_SHARES_OUTSTANDING, 15.0e9),
                      (METRIC_TOTAL_DEBT, 100.0e9),
                      (METRIC_CASH_AND_EQUIVALENTS, 60.0e9)]:
        metrics.append(FundamentalMetric(asset_id, date(2025, 9, 30), "annual",
                                         name, val, "test"))
    fr.upsert_metrics(metrics)
    # Real API: upsert_daily_prices(asset_id, prices: pd.DataFrame, *, source: str)
    # DataFrame must include adjusted_close column.
    price_df = pd.DataFrame([{
        "date": date(2026, 6, 30),
        "open": 200.0, "high": 200.0, "low": 200.0,
        "close": 200.0, "adjusted_close": 200.0,
        "volume": 1_000_000,
    }])
    PriceRepository(conn).upsert_daily_prices(asset_id, price_df, source="test")
    ValuationSnapshotRepository(conn).upsert(ValuationSnapshot(
        asset_id=asset_id, date=date(2026, 6, 30),
        intrinsic_value_per_share=90.0, current_price=200.0, upside_pct=-0.55,
        wacc=0.10, fcf_growth_rate=0.01, terminal_growth_rate=0.025,
        assumptions={"source": "model"}))


def test_compute_persists_normalized_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        seed_us_equities(conn)
        _seed_asset(conn)
        result = compute_and_store_normalized_dcf(
            conn, as_of=date(2026, 6, 30), log=lambda _m: None)
        assert "US_EQ_AAPL" in result.computed
        snap = NormalizedDcfRepository(conn).get("US_EQ_AAPL", date(2026, 6, 30))
        assert snap is not None
        assert snap.valuation_quality in {"ok", "short_history"}
        assert snap.implied_growth is not None
        assert snap.plausibility_gap is not None


def test_compute_skips_asset_without_price(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        seed_us_equities(conn)  # assets exist but no valuation_snapshots / fundamentals
        result = compute_and_store_normalized_dcf(
            conn, as_of=date(2026, 6, 30), log=lambda _m: None)
        assert result.computed == []
        assert all(reason for reason in result.skipped.values())


def test_compute_skips_asset_without_mechanical_wacc(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        seed_us_equities(conn)
        # Seed a price for AAPL but NO valuation_snapshots row.
        price_df = pd.DataFrame([{
            "date": date(2026, 6, 30),
            "open": 200.0, "high": 200.0, "low": 200.0,
            "close": 200.0, "adjusted_close": 200.0,
            "volume": 1_000_000,
        }])
        PriceRepository(conn).upsert_daily_prices("US_EQ_AAPL", price_df, source="test")
        result = compute_and_store_normalized_dcf(
            conn, as_of=date(2026, 6, 30), log=lambda _m: None)
        assert result.skipped.get("US_EQ_AAPL") == "no mechanical wacc"
