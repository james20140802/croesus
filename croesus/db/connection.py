from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb
from dotenv import load_dotenv

DEFAULT_DB_PATH = Path("storage/croesus.duckdb")


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    load_dotenv()
    path = Path(db_path or os.getenv("CROESUS_DB_PATH", DEFAULT_DB_PATH))
    return path


@contextmanager
def get_connection(db_path: str | Path | None = None) -> Iterator[duckdb.DuckDBPyConnection]:
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    try:
        yield conn
    finally:
        conn.close()
