"""Monetary policy surprise analysis.

Surprise proxy: change in 2-year Treasury yield on FOMC day.
  Δ2yr > +threshold_bp  → hawkish_surprise  (market got more-hawkish than expected)
  Δ2yr < -threshold_bp  → dovish_surprise   (market got more-dovish than expected)
  |Δ2yr| ≤ threshold_bp → neutral           (decision in-line with expectations)

Rationale: The 2-year yield is most sensitive to near-term Fed expectations.
A large move on FOMC day (in a tight window) signals the decision surprised the market.
This is a high-frequency identification proxy — not as precise as FF futures implied rates,
but freely available from FRED back to 1976 with no API key required.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.event_study import compute_event_study
from analysis.stats import summarize_category, per_day_stats, compare_categories, variance_comment
from analysis.viz import (
    plot_avg_ar_bar,
    plot_cumulative_car,
    plot_car_histogram,
    plot_category_comparison,
    plot_surprise_scatter,
)

DEFAULT_THRESHOLD_BP = 5.0
SURPRISE_TYPES = ["hawkish_surprise", "neutral", "dovish_surprise"]


def classify_meetings(
    events_df: pd.DataFrame,
    yields: pd.DataFrame,
    threshold_bp: float = DEFAULT_THRESHOLD_BP,
) -> pd.DataFrame:
    """Add delta_2yr_bp and surprise_type columns to events_df.

    Parameters
    ----------
    events_df : DataFrame with 'date' column (datetime.date values)
    yields    : DataFrame indexed by DatetimeIndex, column 'value' (pct, e.g. 3.45)
    threshold_bp : classification threshold in basis points

    Returns
    -------
    Enriched copy of events_df with two new columns:
      delta_2yr_bp  — change in 2yr yield on T=0 vs T-1, in basis points
      surprise_type — 'hawkish_surprise' | 'neutral' | 'dovish_surprise' | 'unknown'
    """
    yield_series = yields["value"].dropna()
    yield_dates = yield_series.index.to_numpy()

    result = events_df.copy()
    delta_bp_list = []
    surprise_list = []

    for event_date in result["date"]:
        event_dt64 = np.datetime64(event_date)
        pos = int(np.searchsorted(yield_dates, event_dt64))

        if pos >= len(yield_dates) or pos < 1:
            delta_bp_list.append(float("nan"))
            surprise_list.append("unknown")
            continue

        y0 = float(yield_series.iloc[pos])
        y1 = float(yield_series.iloc[pos - 1])

        if np.isnan(y0) or np.isnan(y1):
            delta_bp_list.append(float("nan"))
            surprise_list.append("unknown")
            continue

        delta_bp = (y0 - y1) * 100  # pct point → bp
        delta_bp_list.append(delta_bp)

        if delta_bp > threshold_bp:
            surprise_list.append("hawkish_surprise")
        elif delta_bp < -threshold_bp:
            surprise_list.append("dovish_surprise")
        else:
            surprise_list.append("neutral")

    result["delta_2yr_bp"] = delta_bp_list
    result["surprise_type"] = surprise_list
    return result


def run_surprise_analysis(
    category: str,
    events_df: pd.DataFrame,
    prices: pd.DataFrame,
    yields: pd.DataFrame,
    results_dir: Path,
    event_window: tuple[int, int] = (-1, 5),
    estimation_window: tuple[int, int] = (-31, -2),
    threshold_bp: float = DEFAULT_THRESHOLD_BP,
) -> None:
    """Full surprise-based subgroup analysis pipeline.

    1. Classify each meeting by Δ2yr surprise type
    2. Run event study per surprise type
    3. Save CSVs, plots, print summary
    4. Scatter plot: Δ2yr vs AR[T=0] for all meetings
    """
    print(f"\n{'='*60}", file=sys.stderr)
    print(f"[surprise] running surprise analysis for {category}", file=sys.stderr)
    print(f"[surprise] threshold: ±{threshold_bp:.0f}bp", file=sys.stderr)

    surprise_df = classify_meetings(events_df, yields, threshold_bp)
    unknown = (surprise_df["surprise_type"] == "unknown").sum()
    if unknown:
        print(f"[surprise] {unknown} events classified as 'unknown' (yield data missing)", file=sys.stderr)

    surprise_df.to_csv(results_dir / f"{category}_surprise_classified.csv", index=False)

    # ── per-type event study ──────────────────────────────────────────────────
    sg_summaries = []
    all_per_day_with_type = []

    print(f"\n─── {category.upper()} × Surprise 분석 (threshold ±{threshold_bp:.0f}bp) ───")

    for stype in SURPRISE_TYPES:
        subset = surprise_df[surprise_df["surprise_type"] == stype]
        dates = sorted(subset["date"].tolist())
        if not dates:
            print(f"\n  ┌── {stype.upper()}: 이벤트 없음")
            continue

        result = compute_event_study(
            dates, prices,
            event_window=event_window,
            estimation_window=estimation_window,
        )
        sg_per_day = result["per_day"]
        sg_per_event = result["per_event"]

        # annotate type for downstream scatter plot
        sg_per_day["surprise_type"] = stype
        all_per_day_with_type.append(sg_per_day)

        prefix = f"{category}_{stype}"
        sg_per_event.to_csv(results_dir / f"{prefix}_per_event.csv", index=False)

        sg_day_stats = per_day_stats(sg_per_day)
        plot_avg_ar_bar(sg_day_stats, f"{category} [{stype}]", results_dir / f"{prefix}_avg_ar_bar.png")
        plot_cumulative_car(sg_per_day, f"{category} [{stype}]", results_dir / f"{prefix}_cumulative_car.png")
        plot_car_histogram(sg_per_event, f"{category} [{stype}]", results_dir / f"{prefix}_car_histogram.png")

        sg_summary = summarize_category(sg_per_event, sg_per_day, stype)
        sg_summaries.append(sg_summary)

        row = sg_summary.iloc[0]
        delta_mean = float(subset["delta_2yr_bp"].mean())
        print(f"\n  ┌── {stype.upper()} (n={int(row['n'])}, avg Δ2yr={delta_mean:+.1f}bp)")
        print(f"  │   평균 CAR  : {row['mean_CAR']*100:.4f}%")
        print(f"  │   std       : {row['std_CAR']*100:.4f}%")
        print(f"  │   t-stat    : {row['t_stat']:.3f}  p={row['p_value']:.4f}")
        print(f"  └── {variance_comment(row)}")

    # ── surprise comparison chart ─────────────────────────────────────────────
    if sg_summaries:
        sg_comparison = compare_categories(sg_summaries)
        sg_comparison.to_csv(results_dir / f"{category}_surprise_summary.csv", index=False)
        plot_category_comparison(
            sg_comparison,
            results_dir / f"{category}_surprise_comparison.png",
        )

    # ── scatter: Δ2yr vs AR[T=0] ─────────────────────────────────────────────
    # recompute full event study to get AR[T=0] for all events in one pass
    all_dates = sorted(surprise_df[surprise_df["surprise_type"] != "unknown"]["date"].tolist())
    if all_dates:
        full_result = compute_event_study(
            all_dates, prices,
            event_window=event_window,
            estimation_window=estimation_window,
        )
        ar_t0 = (
            full_result["per_day"][full_result["per_day"]["t"] == 0]
            .set_index("event_date")[["AR"]]
            .rename(columns={"AR": "ar_t0"})
        )
        scatter_df = surprise_df.copy()
        scatter_df["event_date"] = pd.to_datetime(scatter_df["date"])
        scatter_df = scatter_df.set_index("event_date").join(ar_t0).reset_index()
        scatter_df = scatter_df.dropna(subset=["delta_2yr_bp", "ar_t0"])

        plot_surprise_scatter(
            scatter_df,
            category=category,
            out_path=results_dir / f"{category}_surprise_scatter.png",
        )
