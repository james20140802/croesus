from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import duckdb

from croesus.quality.models import SEVERITY_ERROR, DataQualityIssue


class DataQualityRepository:
    """Persistence for ``data_quality_issues``."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def record_many(
        self,
        issues: list[DataQualityIssue],
        *,
        run_id: str | None = None,
    ) -> int:
        if not issues:
            return 0
        self.conn.executemany(
            """
            INSERT INTO data_quality_issues
                (issue_id, run_id, domain, severity, asset_id, currency,
                 as_of_date, code, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now())
            """,
            [
                [
                    uuid4().hex,
                    run_id,
                    i.domain,
                    i.severity,
                    i.asset_id,
                    i.currency,
                    i.as_of_date,
                    i.code,
                    i.message,
                ]
                for i in issues
            ],
        )
        return len(issues)

    def list_recent(
        self,
        *,
        severity: str = SEVERITY_ERROR,
        hours: float = 48.0,
        domain: str | None = None,
    ) -> list[DataQualityIssue]:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        sql = """
            SELECT domain, severity, code, message, asset_id, currency, as_of_date
            FROM data_quality_issues
            WHERE severity = ? AND created_at >= ?
        """
        params: list = [severity, cutoff]
        if domain is not None:
            sql += " AND domain = ?"
            params.append(domain)
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [
            DataQualityIssue(
                domain=r[0],
                severity=r[1],
                code=r[2],
                message=r[3],
                asset_id=r[4],
                currency=r[5],
                as_of_date=r[6],
            )
            for r in rows
        ]

    def error_count(self, *, hours: float = 48.0) -> int:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)
        row = self.conn.execute(
            "SELECT COUNT(*) FROM data_quality_issues WHERE severity = ? AND created_at >= ?",
            [SEVERITY_ERROR, cutoff],
        ).fetchone()
        return int(row[0]) if row else 0
