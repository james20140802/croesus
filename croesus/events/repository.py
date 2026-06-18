from __future__ import annotations

from datetime import date

import duckdb

from croesus.events.models import Event


class EventRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, events: list[Event]) -> int:
        """Insert/update events keyed by (asset_id, as_of_date, event_type).

        Idempotent: re-scanning a date overwrites the mutable fields instead of
        duplicating rows. Returns the number of rows submitted.
        """
        if not events:
            return 0
        rows = [
            (
                e.asset_id,
                e.as_of_date,
                e.event_type,
                e.direction,
                e.magnitude,
                e.detail,
                e.source,
            )
            for e in events
        ]
        self.conn.executemany(
            """
            INSERT INTO events (
              asset_id, as_of_date, event_type, direction, magnitude, detail, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, as_of_date, event_type) DO UPDATE SET
              direction = excluded.direction,
              magnitude = excluded.magnitude,
              detail = excluded.detail,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def load_for_date(self, asset_id: str, as_of_date: date) -> list[Event]:
        """Events for one asset on one date (used by downstream methodologies)."""
        result = self.conn.execute(
            """
            SELECT asset_id, as_of_date, event_type, direction, magnitude, detail, source
            FROM events
            WHERE asset_id = ? AND as_of_date = ?
            ORDER BY event_type
            """,
            [asset_id, as_of_date],
        ).fetchall()
        return [
            Event(
                asset_id=row[0],
                as_of_date=row[1],
                event_type=row[2],
                direction=row[3],
                magnitude=row[4],
                detail=row[5],
                source=row[6],
            )
            for row in result
        ]
