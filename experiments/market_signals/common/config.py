from pathlib import Path

# common/config.py -> parents[2] == experiments/market_signals
EXP_DIR = Path(__file__).resolve().parents[1]
# repo root is three levels above experiments/market_signals/common
REPO_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = REPO_ROOT / "storage" / "croesus.duckdb"
RESULTS_DIR = EXP_DIR / "results"
