"""Intraday event impact: computes 2pm→4pm ET return statistics."""
import math

import pandas as pd


def compute_intraday_impact(
    event_dates: list,
    intraday_df: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Statistical summary of 2pm→4pm returns on FOMC event dates.

    Parameters
    ----------
    event_dates : list of date
        FOMC dates to analyze.
    intraday_df : pd.DataFrame
        Output of fetch_intraday_fomc(). Columns: event_date, return_2pm_4pm.

    Returns
    -------
    dict with keys:
        'per_event': DataFrame[event_date, return_2pm_4pm]
        'summary':   DataFrame[n, mean, std, t_stat, p_value]
    """
    _empty_summary = pd.DataFrame([{
        "n": 0, "mean": float("nan"), "std": float("nan"),
        "t_stat": float("nan"), "p_value": float("nan"),
    }])

    if intraday_df.empty or not event_dates:
        return {
            "per_event": pd.DataFrame(columns=["event_date", "return_2pm_4pm"]),
            "summary": _empty_summary,
        }

    per_event = intraday_df[intraday_df["event_date"].isin(event_dates)].copy()
    returns = per_event["return_2pm_4pm"].dropna()
    n = len(returns)

    if n < 2:
        summary = pd.DataFrame([{
            "n": n,
            "mean": float(returns.mean()) if n == 1 else float("nan"),
            "std": float("nan"),
            "t_stat": float("nan"),
            "p_value": float("nan"),
        }])
        return {"per_event": per_event, "summary": summary}

    mean = float(returns.mean())
    std = float(returns.std(ddof=1))
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else float("nan")
    p_value = (
        math.erfc(abs(t_stat) / math.sqrt(2))
        if not math.isnan(t_stat)
        else float("nan")
    )

    summary = pd.DataFrame([{
        "n": n,
        "mean": mean,
        "std": std,
        "t_stat": t_stat,
        "p_value": p_value,
    }])
    return {"per_event": per_event, "summary": summary}
