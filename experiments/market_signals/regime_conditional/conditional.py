"""팩터 롱숏 시계열의 레짐 조건부 분해 + 원형 시프트 placebo.

h=21(월별 리밸런스, 비중첩) 시계열이 1차 대상이라 t는 단순 t-stat.
placebo는 라벨 시계열을 모든 오프셋으로 원형 시프트해(run 구조·지속성 보존)
between-group 분산 통계량의 귀무 분포를 만든다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def join_regime(perdate: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    p = perdate.sort_values("date").copy()
    r = regimes.sort_values("date")[["date", "regime"]].copy()
    # DuckDB(us) vs pandas(ns) 타임스탬프 해상도 불일치 방지
    p["date"] = pd.to_datetime(p["date"]).astype("datetime64[ns]")
    r["date"] = pd.to_datetime(r["date"]).astype("datetime64[ns]")
    out = pd.merge_asof(p, r, on="date", direction="backward")
    return out.dropna(subset=["regime"]).reset_index(drop=True)


def regime_table(joined: pd.DataFrame, ppy: int = 12) -> pd.DataFrame:
    rows = []
    for reg, grp in joined.groupby("regime"):
        x = grp["ls"].to_numpy()
        n = len(x)
        mean = float(x.mean())
        sd = float(x.std(ddof=1)) if n > 1 else np.nan
        ok = n > 1 and sd > 0
        rows.append({"regime": reg, "n": n, "mean": mean,
                     "t": mean / (sd / np.sqrt(n)) if ok else np.nan,
                     "sharpe": mean / sd * np.sqrt(ppy) if ok else np.nan})
    return pd.DataFrame(rows)


def between_stat(returns: np.ndarray, labels: np.ndarray) -> float:
    grand = returns.mean()
    stat = 0.0
    for lab in np.unique(labels):
        x = returns[labels == lab]
        stat += len(x) * (x.mean() - grand) ** 2
    return float(stat / len(returns))


def shift_placebo(returns: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    obs = between_stat(returns, labels)
    n = len(returns)
    hits = sum(between_stat(returns, np.roll(labels, k)) >= obs for k in range(1, n))
    return obs, hits / (n - 1)


def post_change_table(joined: pd.DataFrame) -> pd.DataFrame:
    j = joined.sort_values("date").reset_index(drop=True)
    changed = j["regime"].ne(j["regime"].shift())
    changed.iloc[0] = False  # 첫 관측은 비교 기준이 없어 전환으로 세지 않음
    rows = []
    for name, mask in [("post_change", changed), ("steady", ~changed)]:
        x = j.loc[mask, "ls"].to_numpy()
        n = len(x)
        mean = float(x.mean()) if n else np.nan
        sd = float(x.std(ddof=1)) if n > 1 else np.nan
        rows.append({"phase": name, "n": n, "mean": mean,
                     "t": mean / (sd / np.sqrt(n)) if n > 1 and sd and sd > 0 else np.nan})
    return pd.DataFrame(rows)
