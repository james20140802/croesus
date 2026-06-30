"""Impulse-response estimators for macro-event impact.

caar_curve: event-study CAAR over horizons (the simple IRF).
recovery_horizon + half_life: turn the IRF into magnitude/period numbers.
"""
import datetime

import numpy as np
import pandas as pd


def caar_curve(returns: pd.Series, event_dates, horizons,
               est_window: tuple = (-31, -2)) -> pd.DataFrame:
    dates = returns.index
    est_lo, est_hi = est_window
    per_event = {h: [] for h in horizons}
    for ev in event_dates:
        ev_ts = pd.Timestamp(ev)
        pos = int(dates.searchsorted(ev_ts))
        if pos >= len(dates) or pos + est_lo < 0 or pos + max(horizons) >= len(dates):
            continue
        est = returns.iloc[pos + est_lo: pos + est_hi + 1].dropna()
        if len(est) < 0.8 * (est_hi - est_lo + 1):
            continue
        mu = float(est.mean())
        car = 0.0
        for h in horizons:
            ar = float(returns.iloc[pos + h]) - mu
            car += ar
            per_event[h].append(car)
    rows = []
    for h in horizons:
        vals = np.array(per_event[h], dtype=float)
        if len(vals) == 0:
            continue
        mean = vals.mean()
        se = vals.std(ddof=1) / np.sqrt(len(vals)) if len(vals) > 1 else 0.0
        rows.append({"h": h, "caar": mean, "se": se,
                     "lo": mean - 1.96 * se, "hi": mean + 1.96 * se})
    return pd.DataFrame(rows)


def recovery_horizon(curve: pd.DataFrame):
    if curve.empty:
        return None
    trough_idx = curve["caar"].idxmin()
    after = curve.loc[trough_idx:]
    for _, row in after.iterrows():
        if row["h"] > 0 and row["lo"] <= 0 <= row["hi"]:
            return int(row["h"])
    return None


def half_life(curve: pd.DataFrame):
    if curve.empty:
        return None
    trough_idx = curve["caar"].idxmin()
    seg = curve.loc[trough_idx:, "caar"].values
    if len(seg) < 3:
        return None
    a, b = seg[:-1], seg[1:]
    denom = float(np.dot(a, a))
    if denom == 0:
        return None
    rho = float(np.dot(a, b) / denom)
    if not (0 < rho < 1):
        return None
    return float(np.log(0.5) / np.log(rho))
