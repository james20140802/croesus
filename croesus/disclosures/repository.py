from __future__ import annotations

import duckdb

from croesus.disclosures.models import Disclosure


class DisclosureRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, disclosures: list[Disclosure]) -> int:
        """Insert or update filings keyed by (asset_id, accession_number).

        Idempotent: re-ingesting the same accession overwrites the mutable
        fields rather than duplicating the row. Returns the number of rows
        written.
        """
        if not disclosures:
            return 0
        rows = [
            (
                d.asset_id,
                d.accession_number,
                d.form_type,
                d.filed_date,
                d.report_date,
                d.primary_doc_url,
                d.title,
                d.source,
            )
            for d in disclosures
        ]
        self.conn.executemany(
            """
            INSERT INTO disclosures (
              asset_id, accession_number, form_type, filed_date,
              report_date, primary_doc_url, title, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, accession_number) DO UPDATE SET
              form_type = excluded.form_type,
              filed_date = excluded.filed_date,
              report_date = excluded.report_date,
              primary_doc_url = excluded.primary_doc_url,
              title = excluded.title,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def load_for_asset(self, asset_id: str, *, limit: int = 50) -> list[Disclosure]:
        """Most-recent-first filings for one asset (used by the Phase B2 filter)."""
        result = self.conn.execute(
            """
            SELECT asset_id, accession_number, form_type, filed_date,
                   report_date, primary_doc_url, title, source
            FROM disclosures
            WHERE asset_id = ?
            ORDER BY filed_date DESC, accession_number DESC
            LIMIT ?
            """,
            [asset_id, limit],
        ).fetchall()
        return [
            Disclosure(
                asset_id=row[0],
                accession_number=row[1],
                form_type=row[2],
                filed_date=row[3],
                report_date=row[4],
                primary_doc_url=row[5],
                title=row[6],
                source=row[7],
            )
            for row in result
        ]
