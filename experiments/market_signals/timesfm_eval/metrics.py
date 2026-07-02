"""Model-free forecast-evaluation harness (testable without TimesFM)."""
import numpy as np
import pandas as pd


def directional_hit_rate(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    y_true, y_pred = np.asarray(y_true), np.asarray(y_pred)
    return float(np.mean(np.sign(y_true) == np.sign(y_pred)))


def skill_score(err_model: float, err_baseline: float) -> float:
    if err_baseline == 0:
        return float("nan")
    return 1.0 - err_model / err_baseline


def rolling_origin_eval(series: pd.Series, forecaster, context_len: int,
                        horizons, step: int) -> pd.DataFrame:
    vals = series.values.astype(float)
    n = len(vals)
    hmax = max(horizons)
    rows = []
    for origin in range(context_len, n - hmax, step):
        context = vals[origin - context_len: origin]
        preds = forecaster(context, hmax)
        last = context[-1]
        for h in horizons:
            pred_price = preds[h - 1]
            true_price = vals[origin + h - 1]
            rows.append({
                "origin": origin, "h": h,
                "pred_return": pred_price / last - 1.0,
                "true_return": true_price / last - 1.0,
            })
    return pd.DataFrame(rows)
