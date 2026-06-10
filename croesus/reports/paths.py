"""
Shared report output layout.

Every report writer puts its files under ``reports/<domain>/<YYYY-MM-DD>/`` so
the on-disk tree is consistent across domains (macro, screening, portfolio
action, performance). The filename inside the dated directory stays
domain-specific — macro writes ``macro.md``, screening writes ``<run_id>.md`` —
but the directory shape is identical everywhere, set in one place here.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path


def report_output_dir(
    reports_dir: str | Path, domain: str, as_of_date: date
) -> Path:
    """Return (creating) ``reports_dir/<domain>/<YYYY-MM-DD>/``."""
    out = Path(reports_dir) / domain / as_of_date.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    return out
