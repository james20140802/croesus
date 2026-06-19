from __future__ import annotations

import json
from datetime import date

import duckdb

from croesus.research.thesis_models import ThesisGrade

_COLUMNS = (
    "asset_id", "as_of_date", "run_id", "model", "status",
    "moat_grade", "moat_evidence", "tech_grade", "tech_evidence",
    "sector_grade", "sector_evidence", "disruption_grade", "disruption_evidence",
    "bear_case", "confidence", "evidence_source", "error", "metadata",
)


class ThesisGradeRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, grade: ThesisGrade) -> None:
        """Insert or overwrite the current grade for (asset_id, as_of_date)."""
        self.conn.execute(
            """
            INSERT INTO thesis_grades (
              asset_id, as_of_date, run_id, model, status,
              moat_grade, moat_evidence, tech_grade, tech_evidence,
              sector_grade, sector_evidence, disruption_grade, disruption_evidence,
              bear_case, confidence, evidence_source, error, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, as_of_date) DO UPDATE SET
              run_id = excluded.run_id,
              model = excluded.model,
              status = excluded.status,
              moat_grade = excluded.moat_grade,
              moat_evidence = excluded.moat_evidence,
              tech_grade = excluded.tech_grade,
              tech_evidence = excluded.tech_evidence,
              sector_grade = excluded.sector_grade,
              sector_evidence = excluded.sector_evidence,
              disruption_grade = excluded.disruption_grade,
              disruption_evidence = excluded.disruption_evidence,
              bear_case = excluded.bear_case,
              confidence = excluded.confidence,
              evidence_source = excluded.evidence_source,
              error = excluded.error,
              metadata = excluded.metadata,
              updated_at = now()
            """,
            [
                grade.asset_id, grade.as_of_date, grade.run_id, grade.model,
                grade.status, grade.moat_grade, grade.moat_evidence,
                grade.tech_grade, grade.tech_evidence, grade.sector_grade,
                grade.sector_evidence, grade.disruption_grade,
                grade.disruption_evidence, grade.bear_case, grade.confidence,
                grade.evidence_source, grade.error, json.dumps(grade.metadata),
            ],
        )

    def load_for_asset(self, asset_id: str, as_of: date) -> ThesisGrade | None:
        row = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM thesis_grades "
            "WHERE asset_id = ? AND as_of_date = ?",
            [asset_id, as_of],
        ).fetchone()
        if row is None:
            return None
        data = dict(zip(_COLUMNS, row))
        meta = data.pop("metadata")
        return ThesisGrade(
            metadata=json.loads(meta) if isinstance(meta, str) else (meta or {}),
            **data,
        )
