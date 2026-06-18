from __future__ import annotations

import duckdb

from croesus.disclosures.text_models import (
    STATUS_EMPTY,
    STATUS_FETCHED,
    DisclosureText,
)


class DisclosureTextRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, texts: list[DisclosureText]) -> int:
        """Insert/update filing texts keyed by (asset_id, accession_number).

        Idempotent; returns the number of rows submitted.
        """
        if not texts:
            return 0
        rows = [
            (
                t.asset_id,
                t.accession_number,
                t.source_url,
                t.char_count,
                t.text,
                t.status,
                t.source,
            )
            for t in texts
        ]
        self.conn.executemany(
            """
            INSERT INTO disclosure_texts (
              asset_id, accession_number, source_url, char_count, text, status, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, accession_number) DO UPDATE SET
              source_url = excluded.source_url,
              char_count = excluded.char_count,
              text = excluded.text,
              status = excluded.status,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def accessions_with_text(self, asset_id: str) -> set[str]:
        """Accession numbers that already have usable (non-empty) text stored.

        Used by the ingest job to skip refetching. 'empty'/'failed' rows are
        excluded so a previous miss can be retried.
        """
        result = self.conn.execute(
            """
            SELECT accession_number FROM disclosure_texts
            WHERE asset_id = ? AND status = ?
            """,
            [asset_id, STATUS_FETCHED],
        ).fetchall()
        return {row[0] for row in result}

    def terminal_accessions(self, asset_id: str) -> set[str]:
        """Accessions already resolved terminally — text fetched OR confirmed
        empty (a valid document with no extractable text). The ingest job won't
        refetch these. 'failed' rows (transient fetch errors) are excluded so a
        later run can retry them.
        """
        result = self.conn.execute(
            """
            SELECT accession_number FROM disclosure_texts
            WHERE asset_id = ? AND status IN (?, ?)
            """,
            [asset_id, STATUS_FETCHED, STATUS_EMPTY],
        ).fetchall()
        return {row[0] for row in result}

    def get(self, asset_id: str, accession_number: str) -> DisclosureText | None:
        row = self.conn.execute(
            """
            SELECT asset_id, accession_number, source_url, char_count, text, status, source
            FROM disclosure_texts
            WHERE asset_id = ? AND accession_number = ?
            """,
            [asset_id, accession_number],
        ).fetchone()
        if row is None:
            return None
        return DisclosureText(
            asset_id=row[0],
            accession_number=row[1],
            source_url=row[2],
            char_count=row[3],
            text=row[4],
            status=row[5],
            source=row[6],
        )
