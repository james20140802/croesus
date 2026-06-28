from __future__ import annotations
from pathlib import Path
from croesus.web import create_app


def make_app(db_path: Path | str = "storage/croesus.duckdb"):
    return create_app(db_path)
