from __future__ import annotations

from pathlib import Path

from croesus.db.connection import get_connection

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def migrate(db_path: str | Path | None = None) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        conn.execute(schema)


if __name__ == "__main__":
    migrate()
