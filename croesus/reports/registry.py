"""
Report registry (Sprint 012).

Every report file written by the pipeline should be registered here so the
status dashboard can answer "what is the latest screening report?" without
scanning the filesystem.

Design follows the repository pattern established in
``croesus/quality/repository.py`` — plain functions on a connection rather
than a class, because the registry is a thin write-then-query surface with no
domain logic.

``register_report`` inserts one row.  ``latest_reports`` returns the newest
row per ``report_type`` (max ``created_at``, tie-break max ``report_id``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

import duckdb

# Stable report-type strings — new types append here; existing strings must
# never be renamed because they are stored as primary-key components.
REPORT_TYPE_MACRO = "macro"
REPORT_TYPE_SCREENING = "screening"
REPORT_TYPE_PORTFOLIO_ACTION = "portfolio_action"
REPORT_TYPE_PERFORMANCE = "performance"

_SUFFIX_TO_FORMAT: dict[str, str] = {
    ".md": "markdown",
    ".csv": "csv",
    ".json": "json",
    ".html": "html",
}


def _infer_format(path: str | Path) -> str | None:
    """Return a canonical format name from the file suffix, or None."""
    suffix = Path(path).suffix.lower()
    return _SUFFIX_TO_FORMAT.get(suffix)


@dataclass(frozen=True)
class RegisteredReport:
    """Lightweight view of one ``reports`` row returned by ``latest_reports``."""

    report_type: str
    as_of_date: date | None
    path: str
    fmt: str | None
    run_id: str | None
    created_at: datetime


def register_report(
    conn: duckdb.DuckDBPyConnection,
    *,
    report_type: str,
    path: str | Path,
    as_of_date: date | None = None,
    fmt: str | None = None,
    run_id: str | None = None,
) -> None:
    """Insert one row into the ``reports`` table.

    ``fmt`` is inferred from the path suffix when not provided.
    ``report_id`` is a fresh UUID hex so callers do not need to generate one.
    """
    resolved_fmt = fmt if fmt is not None else _infer_format(path)
    conn.execute(
        """
        INSERT INTO reports (report_id, report_type, as_of_date, path, format, run_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, now())
        """,
        [uuid4().hex, report_type, as_of_date, str(path), resolved_fmt, run_id],
    )


def register_many(
    conn: duckdb.DuckDBPyConnection,
    report_type: str,
    paths: list[str | Path],
    *,
    as_of_date: date | None = None,
    run_id: str | None = None,
) -> None:
    """Register multiple files of the same ``report_type`` in one call.

    Each file's format is inferred from its suffix independently. The batch
    shares one transaction so DuckDB's transaction-scoped now() gives every
    companion file the same created_at — which is what lets latest_reports
    prefer the markdown artifact over its csv twin.
    """
    conn.execute("BEGIN TRANSACTION")
    try:
        for path in paths:
            register_report(
                conn,
                report_type=report_type,
                path=path,
                as_of_date=as_of_date,
                run_id=run_id,
            )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def latest_reports(conn: duckdb.DuckDBPyConnection) -> list[RegisteredReport]:
    """Return the newest row per ``report_type``.

    Ordering: max ``created_at``; companion files written in the same batch
    (markdown + csv) share a timestamp, so ties prefer markdown — the
    human-facing artifact the dashboard should point at — then fall back to
    ``report_id`` for full determinism.
    """
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT
                report_type, as_of_date, path, format, run_id, created_at,
                ROW_NUMBER() OVER (
                    PARTITION BY report_type
                    ORDER BY created_at DESC,
                             (format = 'markdown') DESC,
                             report_id DESC
                ) AS rn
            FROM reports
        )
        SELECT report_type, as_of_date, path, format, run_id, created_at
        FROM ranked
        WHERE rn = 1
        ORDER BY report_type
        """
    ).fetchall()
    return [
        RegisteredReport(
            report_type=row[0],
            as_of_date=row[1],
            path=row[2],
            fmt=row[3],
            run_id=row[4],
            created_at=row[5],
        )
        for row in rows
    ]
