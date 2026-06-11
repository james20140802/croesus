"""
Markdown data-quality block shared by report writers (Sprint 008a).

Reports that present money values (portfolio action, performance) must lead
with unresolved ERROR-level issues so a misstated total is never read as clean.
"""
from __future__ import annotations

import duckdb

from croesus.quality.repository import DataQualityRepository


def data_quality_block(
    conn: duckdb.DuckDBPyConnection,
    *,
    hours: float = 48.0,
) -> list[str]:
    """Return markdown lines for recent ERROR issues, or [] when clean."""
    issues = DataQualityRepository(conn).list_recent(hours=hours)
    if not issues:
        return []
    lines = [
        "## ⚠️ Data Quality — DEGRADED",
        "",
        (
            f"{len(issues)} ERROR-level data-quality issue(s) in the last "
            f"{hours:.0f}h. Values below may be misstated; resolve before acting."
        ),
        "",
    ]
    for issue in issues:
        subject = issue.asset_id or issue.currency or issue.domain
        lines.append(f"- `{issue.code}` {subject}: {issue.message}")
    lines.append("")
    return lines
