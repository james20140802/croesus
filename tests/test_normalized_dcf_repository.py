from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.equity.normalized_repository import (
    NormalizedDcfRepository,
    NormalizedDcfSnapshot,
)


def _snap(asset_id: str, d: date, gap: float) -> NormalizedDcfSnapshot:
    return NormalizedDcfSnapshot(
        asset_id=asset_id, date=d, current_price=100.0,
        normalized_base_fcf=50.0, reference_growth=0.03,
        normalized_intrinsic_value_per_share=80.0, normalized_upside_pct=-0.2,
        implied_growth=0.25, plausibility_gap=gap, valuation_quality="ok",
        n_fcf_years=8, wacc=0.10, assumptions={"source": "model"},
    )


def test_upsert_and_get_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        repo = NormalizedDcfRepository(conn)
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.22))
        got = repo.get("US_EQ_AAPL", date(2026, 6, 30))
        assert got is not None
        assert got.plausibility_gap == 0.22
        assert got.assumptions["source"] == "model"


def test_upsert_overwrites_same_key(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        repo = NormalizedDcfRepository(conn)
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.22))
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.11))
        assert repo.get("US_EQ_AAPL", date(2026, 6, 30)).plausibility_gap == 0.11


def test_load_latest_one_row_per_asset(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        repo = NormalizedDcfRepository(conn)
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 3, 31), 0.5))
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.2))
        repo.upsert(_snap("US_EQ_MSFT", date(2026, 6, 30), 0.3))
        rows = repo.load_latest(date(2026, 6, 30))
        by_asset = {r.asset_id: r for r in rows}
        assert set(by_asset) == {"US_EQ_AAPL", "US_EQ_MSFT"}
        assert by_asset["US_EQ_AAPL"].plausibility_gap == 0.2  # the June row, not March
