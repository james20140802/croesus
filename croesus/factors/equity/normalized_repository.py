"""Persistence for the normalized-FCF reverse-DCF methodology."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

import duckdb

_COLUMNS = (
    "asset_id", "date", "current_price", "normalized_base_fcf", "reference_growth",
    "normalized_intrinsic_value_per_share", "normalized_upside_pct", "implied_growth",
    "plausibility_gap", "valuation_quality", "n_fcf_years", "wacc", "assumptions_json",
)


@dataclass(frozen=True)
class NormalizedDcfSnapshot:
    asset_id: str
    date: date
    current_price: float | None
    normalized_base_fcf: float | None
    reference_growth: float | None
    normalized_intrinsic_value_per_share: float | None
    normalized_upside_pct: float | None
    implied_growth: float | None
    plausibility_gap: float | None
    valuation_quality: str
    n_fcf_years: int
    wacc: float | None
    assumptions: dict = field(default_factory=dict)


class NormalizedDcfRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, snapshot: NormalizedDcfSnapshot) -> None:
        self.conn.execute(
            """
            INSERT INTO normalized_dcf_snapshots (
              asset_id, date, current_price, normalized_base_fcf, reference_growth,
              normalized_intrinsic_value_per_share, normalized_upside_pct,
              implied_growth, plausibility_gap, valuation_quality, n_fcf_years,
              wacc, assumptions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, date) DO UPDATE SET
              current_price = excluded.current_price,
              normalized_base_fcf = excluded.normalized_base_fcf,
              reference_growth = excluded.reference_growth,
              normalized_intrinsic_value_per_share = excluded.normalized_intrinsic_value_per_share,
              normalized_upside_pct = excluded.normalized_upside_pct,
              implied_growth = excluded.implied_growth,
              plausibility_gap = excluded.plausibility_gap,
              valuation_quality = excluded.valuation_quality,
              n_fcf_years = excluded.n_fcf_years,
              wacc = excluded.wacc,
              assumptions_json = excluded.assumptions_json,
              updated_at = now()
            """,
            [
                snapshot.asset_id, snapshot.date, snapshot.current_price,
                snapshot.normalized_base_fcf, snapshot.reference_growth,
                snapshot.normalized_intrinsic_value_per_share,
                snapshot.normalized_upside_pct, snapshot.implied_growth,
                snapshot.plausibility_gap, snapshot.valuation_quality,
                snapshot.n_fcf_years, snapshot.wacc,
                json.dumps(snapshot.assumptions),
            ],
        )

    def _row_to_snapshot(self, row: tuple) -> NormalizedDcfSnapshot:
        data = dict(zip(_COLUMNS, row))
        raw = data.pop("assumptions_json")
        assumptions = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return NormalizedDcfSnapshot(assumptions=assumptions, **data)

    def get(self, asset_id: str, as_of: date) -> NormalizedDcfSnapshot | None:
        row = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM normalized_dcf_snapshots "
            "WHERE asset_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            [asset_id, as_of],
        ).fetchone()
        return None if row is None else self._row_to_snapshot(row)

    def load_latest(self, as_of: date) -> list[NormalizedDcfSnapshot]:
        rows = self.conn.execute(
            f"""
            WITH ranked AS (
                SELECT {', '.join(_COLUMNS)},
                       ROW_NUMBER() OVER (PARTITION BY asset_id ORDER BY date DESC) AS rn
                FROM normalized_dcf_snapshots
                WHERE date <= ?
            )
            SELECT {', '.join(_COLUMNS)} FROM ranked WHERE rn = 1
            """,
            [as_of],
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]
