from pathlib import Path

# experiments/events_impact/config.py → 3 parents up = repo root
REPO_ROOT = Path(__file__).parent.parent.parent
DB_PATH = REPO_ROOT / "storage" / "croesus.duckdb"
RESULTS_DIR = Path(__file__).parent / "results"
EVENTS_DIR = Path(__file__).parent / "events"
