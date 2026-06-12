"""Persistence for research notes (Sprint 010)."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import duckdb

from croesus.research.models import ResearchNote


class ResearchNoteRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def save_many(self, notes: list[ResearchNote]) -> int:
        if not notes:
            return 0
        self.conn.executemany(
            """
            INSERT INTO research_notes
                (note_id, run_id, action_id, asset_id, as_of_date, model, status,
                 business_summary, catalysts, risk_factors,
                 knowledge_cutoff_caveat, error, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?::JSON, now())
            """,
            [
                [
                    n.note_id,
                    n.run_id,
                    n.action_id,
                    n.asset_id,
                    n.as_of_date,
                    n.model,
                    n.status,
                    n.business_summary,
                    n.catalysts,
                    n.risk_factors,
                    n.knowledge_cutoff_caveat,
                    n.error,
                    json.dumps(n.metadata),
                ]
                for n in notes
            ],
        )
        return len(notes)

    def list_for_run(self, run_id: str) -> list[ResearchNote]:
        rows = self.conn.execute(
            """
            SELECT note_id, run_id, action_id, asset_id, as_of_date, model, status,
                   business_summary, catalysts, risk_factors,
                   knowledge_cutoff_caveat, error, metadata
            FROM research_notes
            WHERE run_id = ?
            ORDER BY asset_id, note_id
            """,
            [run_id],
        ).fetchall()
        return [self._row_to_note(row) for row in rows]

    @staticmethod
    def _row_to_note(row: tuple[Any, ...]) -> ResearchNote:
        metadata = row[12]
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        as_of = row[4]
        if isinstance(as_of, datetime):
            as_of = as_of.date()
        return ResearchNote(
            note_id=row[0],
            run_id=row[1],
            action_id=row[2],
            asset_id=row[3],
            as_of_date=as_of,
            model=row[5],
            status=row[6],
            business_summary=row[7],
            catalysts=row[8],
            risk_factors=row[9],
            knowledge_cutoff_caveat=bool(row[10]),
            error=row[11],
            metadata=metadata or {},
        )
