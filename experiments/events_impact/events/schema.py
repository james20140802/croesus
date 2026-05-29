from pathlib import Path
import pandas as pd

REQUIRED_COLUMNS = ["date", "category"]
OPTIONAL_COLUMNS = ["magnitude", "scope", "metadata", "regime", "is_emergency"]
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


def load_events_csv(path: str | Path, category: str) -> pd.DataFrame:
    """Load an events CSV and enforce the standard schema."""
    df = pd.read_csv(path)
    for col in ALL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[ALL_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["category"] = df["category"].fillna(category).astype(str)
    df["magnitude"] = pd.to_numeric(df["magnitude"], errors="coerce")
    df["is_emergency"] = df["is_emergency"].fillna(False).astype(bool)
    df["regime"] = df["regime"].where(df["regime"].notna())
    return df.sort_values("date").reset_index(drop=True)
