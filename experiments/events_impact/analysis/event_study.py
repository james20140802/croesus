"""Category-agnostic event study computation.

compute_event_study() accepts any list of event dates and a price
DataFrame — it has no knowledge of FOMC, tariffs, or any category.
Adding a new event category requires zero changes here.
"""
import datetime
import sys
from typing import Literal

import numpy as np
import pandas as pd


def compute_event_study(
    event_dates: list[datetime.date],
    prices: pd.DataFrame,
    *,
    event_window: tuple[int, int] = (-1, 5),
    estimation_window: tuple[int, int] = (-31, -2),
    ar_method: Literal["mean_adjusted"] = "mean_adjusted",
) -> dict[str, pd.DataFrame]:
    """Compute abnormal returns for each event.

    Parameters
    ----------
    event_dates:
        List of event dates. Non-trading days are mapped to the next
        available trading day.
    prices:
        DataFrame indexed by date (DatetimeIndex), must contain
        'adjusted_close' column.
    event_window:
        (start_offset, end_offset) in trading days relative to T=0.
        Default (-1, 5) = T-1 through T+5, 7 trading days total.
    estimation_window:
        (start_offset, end_offset) for computing expected return.
        Default (-31, -2) = 30 trading days.
    ar_method:
        'mean_adjusted' — expected return = mean of estimation window.
        Other values raise ValueError (reserved for future methods).

    Returns
    -------
    dict with keys:
        'per_day':   DataFrame[event_date, t, actual_return, expected_return, AR]
        'per_event': DataFrame[event_date, CAR]
    """
    if ar_method != "mean_adjusted":
        raise ValueError(
            f"ar_method '{ar_method}' not implemented; supported: mean_adjusted"
        )

    returns = prices["adjusted_close"].pct_change()
    trading_dates = prices.index.to_numpy()

    ev_start, ev_end = event_window
    est_start, est_end = estimation_window

    est_len = est_end - est_start + 1

    per_day_rows = []
    per_event_rows = []
    skipped = 0

    for event_date in event_dates:
        event_dt64 = np.datetime64(event_date)
        pos = int(np.searchsorted(trading_dates, event_dt64))

        if pos >= len(trading_dates):
            print(
                f"[event_study] skip {event_date}: after end of price data",
                file=sys.stderr,
            )
            skipped += 1
            continue

        # check estimation window bounds
        est_lo = pos + est_start
        est_hi = pos + est_end  # inclusive

        if est_lo < 1:  # pos 0 has NaN return; need at least pos 1
            print(
                f"[event_study] skip {event_date}: insufficient estimation window data",
                file=sys.stderr,
            )
            skipped += 1
            continue

        if est_hi >= len(trading_dates):
            print(
                f"[event_study] skip {event_date}: estimation window extends beyond price data",
                file=sys.stderr,
            )
            skipped += 1
            continue

        est_returns = returns.iloc[est_lo : est_hi + 1].dropna()
        if len(est_returns) < est_len * 0.8:
            # require at least 80% of estimation window to be non-NaN
            print(
                f"[event_study] skip {event_date}: too many NaN in estimation window",
                file=sys.stderr,
            )
            skipped += 1
            continue

        expected_return = float(est_returns.mean())

        # event window
        car = 0.0
        event_ok = True
        for t in range(ev_start, ev_end + 1):
            t_pos = pos + t
            if t_pos < 0 or t_pos >= len(trading_dates):
                event_ok = False
                break
            actual_return = float(returns.iloc[t_pos])
            if np.isnan(actual_return):
                event_ok = False
                break
            ar = actual_return - expected_return
            car += ar
            per_day_rows.append({
                "event_date": event_date,
                "t": t,
                "actual_return": actual_return,
                "expected_return": expected_return,
                "AR": ar,
            })

        if not event_ok:
            # remove the partial rows we just added
            per_day_rows = [r for r in per_day_rows if r["event_date"] != event_date]
            print(
                f"[event_study] skip {event_date}: incomplete event window",
                file=sys.stderr,
            )
            skipped += 1
            continue

        per_event_rows.append({"event_date": event_date, "CAR": car})

    if skipped:
        print(f"[event_study] skipped {skipped} events total", file=sys.stderr)

    per_day = pd.DataFrame(per_day_rows) if per_day_rows else pd.DataFrame(
        columns=["event_date", "t", "actual_return", "expected_return", "AR"]
    )
    per_event = pd.DataFrame(per_event_rows) if per_event_rows else pd.DataFrame(
        columns=["event_date", "CAR"]
    )

    return {"per_day": per_day, "per_event": per_event}
