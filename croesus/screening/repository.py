from __future__ import annotations

import json
from typing import Any

import duckdb

from croesus.screening.models import ScreeningCandidate


class ScreeningRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_results(self, candidates: list[ScreeningCandidate]) -> None:
        if not candidates:
            return
        self.conn.executemany(
            """
            INSERT INTO screening_results (
              run_id, asset_id, score, rank, decision_bucket, reason,
              reason_codes, factor_scores, metadata
            )
            VALUES (?, ?, ?, ?, ?, ?, ?::JSON, ?::JSON, ?::JSON)
            ON CONFLICT (run_id, asset_id) DO UPDATE SET
              score = excluded.score,
              rank = excluded.rank,
              decision_bucket = excluded.decision_bucket,
              reason = excluded.reason,
              reason_codes = excluded.reason_codes,
              factor_scores = excluded.factor_scores,
              metadata = excluded.metadata
            """,
            [self._candidate_to_params(candidate) for candidate in candidates],
        )

    def list_results(self, run_id: str) -> list[ScreeningCandidate]:
        rows = self.conn.execute(
            """
            SELECT run_id, asset_id, score, rank, decision_bucket, reason,
                   reason_codes, factor_scores, metadata
            FROM screening_results
            WHERE run_id = ?
            ORDER BY
              CASE WHEN rank IS NULL THEN 1 ELSE 0 END,
              rank,
              asset_id
            """,
            [run_id],
        ).fetchall()
        columns = [desc[0] for desc in self.conn.description]
        return [self._row_to_candidate(dict(zip(columns, row))) for row in rows]

    @staticmethod
    def _candidate_to_params(candidate: ScreeningCandidate) -> tuple[Any, ...]:
        return (
            candidate.run_id,
            candidate.asset_id,
            candidate.score,
            candidate.rank,
            candidate.decision_bucket,
            candidate.reason,
            json.dumps(candidate.reason_codes),
            json.dumps(candidate.factor_scores),
            json.dumps(candidate.metadata),
        )

    @staticmethod
    def _row_to_candidate(row: dict[str, Any]) -> ScreeningCandidate:
        return ScreeningCandidate(
            run_id=row["run_id"],
            asset_id=row["asset_id"],
            score=row["score"],
            rank=row["rank"],
            decision_bucket=row["decision_bucket"],
            reason=row["reason"],
            reason_codes=_loads(row.get("reason_codes"), []),
            factor_scores=_loads(row.get("factor_scores"), {}),
            metadata=_loads(row.get("metadata"), {}),
        )


def _loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)
