"""FOMC Event Study — pipeline entrypoint.

Usage:
    cd experiments/events_impact
    pip install -r requirements.txt
    python main.py

To add a new category:
    1. Create events/<category>.csv (columns: date, category, magnitude, scope, metadata)
    2. Create events/<category>.py exposing get_events() -> pd.DataFrame
    3. Add an entry to CATEGORIES below
    4. Re-run main.py
"""
import datetime
import sys
from pathlib import Path

import pandas as pd

from config import RESULTS_DIR
from events import fomc, dummy_macro
from data.prices import fetch_prices
from data.rates import fetch_2yr_yield
from analysis.event_study import compute_event_study
from analysis.stats import summarize_category, per_day_stats, compare_categories, variance_comment
from analysis.surprise import run_surprise_analysis
from analysis.viz import (
    plot_avg_ar_bar,
    plot_cumulative_car,
    plot_car_histogram,
    plot_category_comparison,
)

# ── Category registry ─────────────────────────────────────────────────────────
# Add new categories here. All other code stays the same.
# event_window / estimation_window can be overridden per category.
CATEGORIES = {
    "fomc": {
        "loader": fomc.get_events,
        "target": "^GSPC",
        "asset_id": "US_IDX_SP500",
        # magnitude > 0 = hike, == 0 (or NaN) = hold, < 0 = cut
        "subgroups": {
            "hike": lambda df: df[df["magnitude"].fillna(0) > 0],
            "hold": lambda df: df[df["magnitude"].fillna(0) == 0],
            "cut":  lambda df: df[df["magnitude"].fillna(0) < 0],
        },
    },
    "dummy_macro": {
        "loader": dummy_macro.get_events,
        "target": "^GSPC",
        "asset_id": "US_IDX_SP500",
    },
}

# Shared window defaults (can be overridden per-category in CATEGORIES dict)
DEFAULT_EVENT_WINDOW = (-14, 10)
DEFAULT_ESTIMATION_WINDOW = (-80, -15)  # must end before event_window starts (-14)
# ─────────────────────────────────────────────────────────────────────────────


def _price_range(
    event_dates: list[datetime.date],
    estimation_window: tuple[int, int],
    event_window: tuple[int, int],
) -> tuple[datetime.date, datetime.date]:
    """Compute price fetch range with generous buffer for trading-day offsets."""
    cal_buffer_start = abs(estimation_window[0]) * 2  # ~2x to handle holidays
    cal_buffer_end = event_window[1] * 2
    price_start = min(event_dates) - datetime.timedelta(days=cal_buffer_start)
    price_end = max(event_dates) + datetime.timedelta(days=cal_buffer_end)
    return price_start, price_end


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    summaries: list[pd.DataFrame] = []
    # store events_df + prices per category for post-loop analyses
    category_data: dict[str, dict] = {}

    for category, cfg in CATEGORIES.items():
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"[main] processing category: {category}", file=sys.stderr)

        event_window = cfg.get("event_window", DEFAULT_EVENT_WINDOW)
        estimation_window = cfg.get("estimation_window", DEFAULT_ESTIMATION_WINDOW)

        # 1. Load events
        events_df = cfg["loader"]()
        event_dates = sorted(events_df["date"].tolist())
        print(f"[main] {len(event_dates)} event dates loaded", file=sys.stderr)

        # 2. Fetch prices
        price_start, price_end = _price_range(event_dates, estimation_window, event_window)
        prices = fetch_prices(cfg["asset_id"], cfg["target"], price_start, price_end)
        category_data[category] = {
            "events_df": events_df, "prices": prices,
            "event_window": event_window, "estimation_window": estimation_window,
        }

        # 3. Compute event study
        result = compute_event_study(
            event_dates,
            prices,
            event_window=event_window,
            estimation_window=estimation_window,
        )
        per_day = result["per_day"]
        per_event = result["per_event"]
        print(f"[main] {len(per_event)} events computed", file=sys.stderr)

        # 4. Save CSVs
        per_event.to_csv(RESULTS_DIR / f"{category}_per_event.csv", index=False)
        per_day.to_csv(RESULTS_DIR / f"{category}_per_day.csv", index=False)

        # 5. Aggregate stats
        summary = summarize_category(per_event, per_day, category)
        summaries.append(summary)
        day_stats = per_day_stats(per_day)
        day_stats.to_csv(RESULTS_DIR / f"{category}_day_stats.csv", index=False)

        # 6. Visualize
        plot_avg_ar_bar(day_stats, category, RESULTS_DIR / f"{category}_avg_ar_bar.png")
        plot_cumulative_car(per_day, category, RESULTS_DIR / f"{category}_cumulative_car.png")
        plot_car_histogram(per_event, category, RESULTS_DIR / f"{category}_car_histogram.png")

        # 7. Print summary
        row = summary.iloc[0]
        comment = variance_comment(row)
        print(f"\n─── {category.upper()} 결과 ───")
        print(f"  이벤트 수       : {int(row['n'])}")
        print(f"  평균 CAR        : {row['mean_CAR']*100:.4f}%")
        print(f"  표준편차 (CAR)  : {row['std_CAR']*100:.4f}%")
        print(f"  t-statistic     : {row['t_stat']:.3f}")
        print(f"  p-value         : {row['p_value']:.4f}")
        print(f"  분산 코멘트     : {comment}")

        # 8. Subgroup analysis (optional — only if 'subgroups' defined in cfg)
        subgroups = cfg.get("subgroups", {})
        if subgroups:
            sg_summaries: list[pd.DataFrame] = []
            for sg_name, filter_fn in subgroups.items():
                sg_events = filter_fn(events_df)
                sg_dates = sorted(sg_events["date"].tolist())
                if not sg_dates:
                    print(f"[main] subgroup {sg_name}: 0 events, skip", file=sys.stderr)
                    continue
                print(f"[main] subgroup {sg_name}: {len(sg_dates)} events", file=sys.stderr)

                sg_result = compute_event_study(
                    sg_dates, prices,
                    event_window=event_window,
                    estimation_window=estimation_window,
                )
                sg_per_day = sg_result["per_day"]
                sg_per_event = sg_result["per_event"]

                prefix = f"{category}_{sg_name}"
                sg_per_event.to_csv(RESULTS_DIR / f"{prefix}_per_event.csv", index=False)
                sg_per_day.to_csv(RESULTS_DIR / f"{prefix}_per_day.csv", index=False)

                sg_day_stats = per_day_stats(sg_per_day)
                plot_avg_ar_bar(sg_day_stats, f"{category} [{sg_name}]", RESULTS_DIR / f"{prefix}_avg_ar_bar.png")
                plot_cumulative_car(sg_per_day, f"{category} [{sg_name}]", RESULTS_DIR / f"{prefix}_cumulative_car.png")
                plot_car_histogram(sg_per_event, f"{category} [{sg_name}]", RESULTS_DIR / f"{prefix}_car_histogram.png")

                sg_summary = summarize_category(sg_per_event, sg_per_day, sg_name)
                sg_summaries.append(sg_summary)

                sg_row = sg_summary.iloc[0]
                print(f"\n  ┌── {sg_name.upper()} (n={int(sg_row['n'])})")
                print(f"  │   평균 CAR      : {sg_row['mean_CAR']*100:.4f}%")
                print(f"  │   std           : {sg_row['std_CAR']*100:.4f}%")
                print(f"  │   t-stat        : {sg_row['t_stat']:.3f}  p={sg_row['p_value']:.4f}")
                print(f"  └── {variance_comment(sg_row)}")

            if sg_summaries:
                sg_comparison = compare_categories(sg_summaries)
                sg_comparison.to_csv(RESULTS_DIR / f"{category}_subgroup_summary.csv", index=False)
                plot_category_comparison(sg_comparison, RESULTS_DIR / f"{category}_subgroup_comparison.png")

    # 9. Cross-category comparison
    if summaries:
        comparison = compare_categories(summaries)
        comparison.to_csv(RESULTS_DIR / "all_categories_summary.csv", index=False)
        plot_category_comparison(comparison, RESULTS_DIR / "category_comparison.png")

    # 10. Surprise analysis (FOMC only — requires Δ2yr as expectation proxy)
    if "fomc" in category_data:
        cd = category_data["fomc"]
        all_dates = sorted(cd["events_df"]["date"].tolist())
        price_start, price_end = _price_range(all_dates, cd["estimation_window"], cd["event_window"])
        try:
            yields = fetch_2yr_yield(price_start, price_end)
            run_surprise_analysis(
                category="fomc",
                events_df=cd["events_df"],
                prices=cd["prices"],
                yields=yields,
                results_dir=RESULTS_DIR,
                event_window=cd["event_window"],
                estimation_window=cd["estimation_window"],
            )
        except RuntimeError as e:
            print(f"\n[main] surprise 분석 건너뜀 (FRED 연결 실패): {e}")

    print(f"\n[main] 결과 저장 위치: {RESULTS_DIR.resolve()}")


if __name__ == "__main__":
    main()
