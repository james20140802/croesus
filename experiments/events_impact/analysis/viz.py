"""Visualization for event study results.

Four plot types:
1. plot_avg_ar_bar    — per-day average AR (bar chart)
2. plot_cumulative_car — cumulative avg AR + 95% CI band (line chart)
3. plot_car_histogram — CAR distribution (histogram)
4. plot_category_comparison — mean CAR comparison across categories (bar chart)
"""
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def plot_avg_ar_bar(
    day_stats: pd.DataFrame,
    category: str,
    out_path: Path,
) -> None:
    """Bar chart: average abnormal return per event-window day."""
    fig, ax = plt.subplots(figsize=(9, 5))
    t_vals = day_stats["t"].values
    means = day_stats["mean_AR"].values * 100  # to percentage

    colors = ["#d32f2f" if m < 0 else "#388e3c" for m in means]
    ax.bar(t_vals, means, color=colors, edgecolor="white", linewidth=0.5)
    ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Event-window day (T=0: event date)")
    ax.set_ylabel("Average abnormal return (%)")
    ax.set_title(f"{category.upper()} — Average AR per Event-Window Day")
    ax.set_xticks(t_vals)
    ax.set_xticklabels([f"T{t:+d}" for t in t_vals])
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_cumulative_car(
    per_day: pd.DataFrame,
    category: str,
    out_path: Path,
) -> None:
    """Line chart: cumulative average AR with 95% CI band.

    CI is computed from the distribution of per-event cumulative ARs
    at each day offset — more statistically sound than accumulating std_err.
    """
    if per_day.empty:
        return

    per_day = per_day.sort_values(["event_date", "t"]).copy()
    per_day["cumAR"] = per_day.groupby("event_date")["AR"].cumsum()

    t_vals = sorted(per_day["t"].unique())
    mean_cum = []
    ci_lo = []
    ci_hi = []

    for t in t_vals:
        subset = per_day[per_day["t"] == t]["cumAR"].dropna()
        n = len(subset)
        mean_c = float(subset.mean())
        std_c = float(subset.std(ddof=1)) if n > 1 else 0.0
        se = std_c / np.sqrt(n) if n > 0 else 0.0
        mean_cum.append(mean_c * 100)
        ci_lo.append((mean_c - 1.96 * se) * 100)
        ci_hi.append((mean_c + 1.96 * se) * 100)

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.fill_between(t_vals, ci_lo, ci_hi, alpha=0.2, color="#1976d2", label="95% CI")
    ax.plot(t_vals, mean_cum, color="#1976d2", linewidth=2, marker="o", markersize=4, label="Avg CAAR")
    ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xlabel("Event-window day (T=0: event date)")
    ax.set_ylabel("Cumulative average abnormal return (%)")
    ax.set_title(f"{category.upper()} — Cumulative Average AR + 95% CI")
    ax.set_xticks(t_vals)
    ax.set_xticklabels([f"T{t:+d}" for t in t_vals])
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_car_histogram(
    per_event: pd.DataFrame,
    category: str,
    out_path: Path,
) -> None:
    """Histogram of per-event CAR distribution."""
    cars = per_event["CAR"].dropna() * 100  # to percentage
    if cars.empty:
        return

    fig, ax = plt.subplots(figsize=(9, 5))
    n_bins = max(10, min(30, len(cars) // 5))
    ax.hist(cars, bins=n_bins, color="#5c6bc0", edgecolor="white", linewidth=0.5)
    ax.axvline(float(cars.mean()), color="#d32f2f", linewidth=1.5,
               linestyle="--", label=f"Mean={cars.mean():.2f}%")
    ax.set_xlabel("CAR (%)")
    ax.set_ylabel("Count")
    ax.set_title(f"{category.upper()} — CAR Distribution (n={len(cars)})")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def plot_category_comparison(
    comparison: pd.DataFrame,
    out_path: Path,
) -> None:
    """Bar chart comparing mean CAR across categories with ±1 std_CAR error bars."""
    if comparison.empty:
        return
    df = comparison.dropna(subset=["mean_CAR"])
    if df.empty:
        return

    fig, ax = plt.subplots(figsize=(max(6, len(df) * 2), 5))
    x = np.arange(len(df))
    means = df["mean_CAR"].values * 100
    yerr = df["std_CAR"].values * 100 / np.sqrt(df["n"].values)  # std_err for CI
    colors = ["#d32f2f" if m < 0 else "#388e3c" for m in means]

    ax.bar(x, means, color=colors, edgecolor="white", linewidth=0.5,
           yerr=1.96 * yerr, capsize=5, error_kw={"linewidth": 1.2})
    ax.axhline(0, color="black", linewidth=0.8, linestyle="--")
    ax.set_xticks(x)
    ax.set_xticklabels(df["category"].values, rotation=15)
    ax.set_ylabel("Mean CAR (%) ± 95% CI")
    ax.set_title("Event Study — Category Comparison")

    # annotate n
    for i, row in enumerate(df.itertuples()):
        ax.text(i, means[i] + np.sign(means[i]) * 0.05, f"n={row.n}",
                ha="center", va="bottom" if means[i] >= 0 else "top", fontsize=8)

    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
