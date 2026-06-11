from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import duckdb


@dataclass(frozen=True)
class ValuationSnapshot:
    asset_id: str
    date: date
    intrinsic_value_per_share: float | None
    current_price: float | None
    upside_pct: float | None
    wacc: float | None
    fcf_growth_rate: float | None
    terminal_growth_rate: float | None
    assumptions: dict


class ValuationSnapshotRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, snapshot: ValuationSnapshot) -> None:
        self.conn.execute(
            """
            INSERT INTO valuation_snapshots (
              asset_id, date, intrinsic_value_per_share, current_price, upside_pct,
              wacc, fcf_growth_rate, terminal_growth_rate, assumptions_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, date) DO UPDATE SET
              intrinsic_value_per_share = excluded.intrinsic_value_per_share,
              current_price = excluded.current_price,
              upside_pct = excluded.upside_pct,
              wacc = excluded.wacc,
              fcf_growth_rate = excluded.fcf_growth_rate,
              terminal_growth_rate = excluded.terminal_growth_rate,
              assumptions_json = excluded.assumptions_json
            """,
            [
                snapshot.asset_id,
                snapshot.date,
                snapshot.intrinsic_value_per_share,
                snapshot.current_price,
                snapshot.upside_pct,
                snapshot.wacc,
                snapshot.fcf_growth_rate,
                snapshot.terminal_growth_rate,
                json.dumps(snapshot.assumptions),
            ],
        )

    def get(self, asset_id: str, as_of: date) -> ValuationSnapshot | None:
        row = self.conn.execute(
            """
            SELECT asset_id, date, intrinsic_value_per_share, current_price,
                   upside_pct, wacc, fcf_growth_rate, terminal_growth_rate,
                   assumptions_json
            FROM valuation_snapshots
            WHERE asset_id = ? AND date <= ?
            ORDER BY date DESC
            LIMIT 1
            """,
            [asset_id, as_of],
        ).fetchone()
        if row is None:
            return None
        assumptions = row[8]
        if isinstance(assumptions, str):
            assumptions = json.loads(assumptions)
        return ValuationSnapshot(
            asset_id=row[0],
            date=row[1],
            intrinsic_value_per_share=row[2],
            current_price=row[3],
            upside_pct=row[4],
            wacc=row[5],
            fcf_growth_rate=row[6],
            terminal_growth_rate=row[7],
            assumptions=assumptions or {},
        )
