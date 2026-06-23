from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from croesus.db.connection import resolve_db_path


class DataUpdatingError(RuntimeError):
    """DuckDB 파일이 다른 프로세스(데일리 싱크)에 의해 잠겨 있을 때."""


@contextmanager
def _connect(db_path, *, read_only: bool) -> Iterator[duckdb.DuckDBPyConnection]:
    path = resolve_db_path(db_path)
    try:
        conn = duckdb.connect(str(path), read_only=read_only)
    except duckdb.Error as exc:  # IOException 포함 — 락/사용중
        raise DataUpdatingError(str(exc)) from exc
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_read_connection(db_path: str | Path | None = None):
    with _connect(db_path, read_only=True) as conn:
        yield conn


@contextmanager
def get_write_connection(db_path: str | Path | None = None):
    with _connect(db_path, read_only=False) as conn:
        yield conn
