from pathlib import Path

from croesus.db.connection import get_connection
from croesus.jobs.bootstrap import main as bootstrap_main


def test_bootstrap_job_uses_configured_db_path_and_seeds_assets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "configured.duckdb"
    monkeypatch.setenv("CROESUS_DB_PATH", str(db_path))

    bootstrap_main()

    with get_connection(db_path) as conn:
        symbols = [
            row[0]
            for row in conn.execute("SELECT symbol FROM assets ORDER BY asset_id").fetchall()
        ]

    assert symbols == ["AAPL", "MSFT", "NVDA"]
