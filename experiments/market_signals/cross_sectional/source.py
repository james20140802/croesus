"""Resolve and open the *source* DuckDB (read-only).

The worktree's own storage/croesus.duckdb only holds the index prices cached by
the first experiment. The real universe (523 equities, full history) lives in the
main checkout. We therefore resolve the source DB explicitly and always open it
read-only so a concurrent web-server/write process is never blocked.
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

from experiments.market_signals.common.config import REPO_ROOT


def source_db_path() -> Path:
    """Prefer $CROESUS_SOURCE_DB, then the main checkout, then the worktree DB."""
    env = os.environ.get("CROESUS_SOURCE_DB")
    if env:
        return Path(env)
    # REPO_ROOT is the worktree root (.../croesus/.claude/worktrees/<wt>); the
    # main checkout with real data sits three levels up.
    candidates = [
        REPO_ROOT.parents[2] / "storage" / "croesus.duckdb",
        REPO_ROOT / "storage" / "croesus.duckdb",
    ]
    for cand in candidates:
        if cand.exists() and cand.stat().st_size > 10_000_000:
            return cand
    return REPO_ROOT / "storage" / "croesus.duckdb"


def connect_source() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(source_db_path()), read_only=True)
