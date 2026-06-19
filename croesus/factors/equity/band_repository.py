from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

_COLUMNS = (
    "asset_id", "date", "scenario", "intrinsic_value_per_share", "current_price",
    "upside_pct", "wacc", "fcf_growth_rate", "terminal_growth_rate",
    "explicit_years", "wacc_risk_premium", "moat_grade", "sector_grade",
    "disruption_grade", "thesis_as_of_date", "thesis_run_id",
)


@dataclass(frozen=True)
class BandRow:
    asset_id: str
    date: date
    scenario: str
    intrinsic_value_per_share: float | None
    current_price: float | None
    upside_pct: float | None
    wacc: float | None
    fcf_growth_rate: float | None
    terminal_growth_rate: float | None
    explicit_years: int | None
    wacc_risk_premium: float | None
    moat_grade: str | None
    sector_grade: str | None
    disruption_grade: str | None
    thesis_as_of_date: date | None
    thesis_run_id: str | None


class IntrinsicValueBandRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_band(self, row: BandRow) -> None:
        self.conn.execute(
            """
            INSERT INTO intrinsic_value_bands (
              asset_id, date, scenario, intrinsic_value_per_share, current_price,
              upside_pct, wacc, fcf_growth_rate, terminal_growth_rate,
              explicit_years, wacc_risk_premium, moat_grade, sector_grade,
              disruption_grade, thesis_as_of_date, thesis_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, date, scenario) DO UPDATE SET
              intrinsic_value_per_share = excluded.intrinsic_value_per_share,
              current_price = excluded.current_price,
              upside_pct = excluded.upside_pct,
              wacc = excluded.wacc,
              fcf_growth_rate = excluded.fcf_growth_rate,
              terminal_growth_rate = excluded.terminal_growth_rate,
              explicit_years = excluded.explicit_years,
              wacc_risk_premium = excluded.wacc_risk_premium,
              moat_grade = excluded.moat_grade,
              sector_grade = excluded.sector_grade,
              disruption_grade = excluded.disruption_grade,
              thesis_as_of_date = excluded.thesis_as_of_date,
              thesis_run_id = excluded.thesis_run_id,
              updated_at = now()
            """,
            [
                row.asset_id, row.date, row.scenario, row.intrinsic_value_per_share,
                row.current_price, row.upside_pct, row.wacc, row.fcf_growth_rate,
                row.terminal_growth_rate, row.explicit_years, row.wacc_risk_premium,
                row.moat_grade, row.sector_grade, row.disruption_grade,
                row.thesis_as_of_date, row.thesis_run_id,
            ],
        )

    def load_for_asset(self, asset_id: str, as_of: date) -> list[BandRow]:
        rows = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM intrinsic_value_bands "
            "WHERE asset_id = ? AND date = ? ORDER BY scenario",
            [asset_id, as_of],
        ).fetchall()
        return [BandRow(**dict(zip(_COLUMNS, r))) for r in rows]
