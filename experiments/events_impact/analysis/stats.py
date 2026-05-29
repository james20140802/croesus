"""Aggregation statistics for event study results."""
import math

import numpy as np
import pandas as pd


def _p_value_normal(t_stat: float) -> float:
    """Two-sided p-value using normal approximation (no scipy dependency)."""
    return math.erfc(abs(t_stat) / math.sqrt(2))


def summarize_category(
    per_event: pd.DataFrame,
    per_day: pd.DataFrame,
    category: str,
) -> pd.DataFrame:
    """One-row summary for a category: n, mean_CAR, std_CAR, t_stat, p_value."""
    cars = per_event["CAR"].dropna()
    n = len(cars)
    if n == 0:
        return pd.DataFrame([{
            "category": category, "n": 0,
            "mean_CAR": float("nan"), "std_CAR": float("nan"),
            "t_stat": float("nan"), "p_value": float("nan"),
            "min_CAR": float("nan"), "max_CAR": float("nan"),
        }])

    mean_car = float(cars.mean())
    std_car = float(cars.std(ddof=1))
    t_stat = mean_car / (std_car / math.sqrt(n)) if std_car > 0 else float("nan")
    p_value = _p_value_normal(t_stat) if not math.isnan(t_stat) else float("nan")

    return pd.DataFrame([{
        "category": category,
        "n": n,
        "mean_CAR": mean_car,
        "std_CAR": std_car,
        "t_stat": t_stat,
        "p_value": p_value,
        "min_CAR": float(cars.min()),
        "max_CAR": float(cars.max()),
    }])


def per_day_stats(per_day: pd.DataFrame) -> pd.DataFrame:
    """Per-day-offset stats: mean_AR, std_AR, n, std_err (for CI band)."""
    if per_day.empty:
        return pd.DataFrame(columns=["t", "mean_AR", "std_AR", "n", "std_err"])
    grouped = per_day.groupby("t")["AR"]
    result = grouped.agg(
        mean_AR="mean",
        std_AR=lambda x: x.std(ddof=1),
        n="count",
    ).reset_index()
    result["std_err"] = result["std_AR"] / np.sqrt(result["n"])
    return result.sort_values("t").reset_index(drop=True)


def compare_categories(summaries: list[pd.DataFrame]) -> pd.DataFrame:
    """Combine per-category summary rows into one comparison table."""
    if not summaries:
        return pd.DataFrame()
    return pd.concat(summaries, ignore_index=True)


def variance_comment(summary_row: pd.Series) -> str:
    """Human-readable comment on statistical significance and dispersion."""
    t = summary_row.get("t_stat", float("nan"))
    mean = summary_row.get("mean_CAR", float("nan"))
    std = summary_row.get("std_CAR", float("nan"))

    if any(math.isnan(v) for v in [t, mean, std]):
        return "데이터 부족으로 평가 불가"

    if abs(t) > 2:
        sig = f"통계적으로 유의 (t={t:.2f}, |t|>2)"
    elif abs(t) > 1.64:
        sig = f"경계선상 유의 (t={t:.2f}, 1.64<|t|<2)"
    else:
        sig = f"통계적으로 비유의 (t={t:.2f}, |t|<1.64)"

    cv = abs(std / mean) if abs(mean) > 1e-10 else float("inf")
    if cv > 3:
        disp = f"이벤트 간 편차 매우 큼 (CV={cv:.1f})"
    elif cv > 1.5:
        disp = f"이벤트 간 편차 큼 (CV={cv:.1f})"
    else:
        disp = f"이벤트 간 편차 보통 이하 (CV={cv:.1f})"

    return f"{sig} | {disp}"
