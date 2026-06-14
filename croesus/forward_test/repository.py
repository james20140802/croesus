"""Persistence for forward-test cohorts."""
from __future__ import annotations

from datetime import date

import duckdb

from croesus.forward_test.models import CohortPick


class ForwardTestRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def save_cohort(self, picks: list[CohortPick]) -> None:
        """Upsert one cohort's picks. Re-recording a (scheme, date) replaces it."""
        for p in picks:
            self.conn.execute(
                """
                INSERT INTO forward_test_cohorts (
                  cohort_scheme, as_of_date, asset_id, rank, score, weight, entry_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (cohort_scheme, as_of_date, asset_id) DO UPDATE SET
                  rank = excluded.rank,
                  score = excluded.score,
                  weight = excluded.weight,
                  entry_price = excluded.entry_price,
                  created_at = now()
                """,
                [
                    p.cohort_scheme, p.as_of_date, p.asset_id,
                    p.rank, p.score, p.weight, p.entry_price,
                ],
            )

    def load_cohort(self, scheme: str, as_of_date: date) -> list[CohortPick]:
        rows = self.conn.execute(
            """
            SELECT cohort_scheme, as_of_date, asset_id, rank, score, weight, entry_price
            FROM forward_test_cohorts
            WHERE cohort_scheme = ? AND as_of_date = ?
            ORDER BY rank
            """,
            [scheme, as_of_date],
        ).fetchall()
        return [_row_to_pick(r) for r in rows]

    def cohort_dates(self, scheme: str | None = None) -> list[tuple[str, date]]:
        """Distinct (scheme, as_of_date) cohorts, oldest first."""
        sql = "SELECT DISTINCT cohort_scheme, as_of_date FROM forward_test_cohorts"
        params: list[object] = []
        if scheme is not None:
            sql += " WHERE cohort_scheme = ?"
            params.append(scheme)
        sql += " ORDER BY as_of_date, cohort_scheme"
        return [(r[0], r[1]) for r in self.conn.execute(sql, params).fetchall()]


def _row_to_pick(row: tuple) -> CohortPick:
    scheme, as_of, asset_id, rank, score, weight, entry = row
    return CohortPick(
        cohort_scheme=scheme,
        as_of_date=as_of,
        asset_id=asset_id,
        rank=rank,
        score=score,
        weight=weight,
        entry_price=entry,
    )
