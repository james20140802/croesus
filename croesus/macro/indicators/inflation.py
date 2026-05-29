from __future__ import annotations

import numpy as np
import pandas as pd


def _trailing_slope(series: pd.Series, window: int = 3) -> float | None:
    vals = series.dropna().tail(window)
    if len(vals) < 2:
        return None
    x = np.arange(len(vals), dtype=float)
    return float(np.polyfit(x, vals.values, 1)[0])


def compute_inflation_direction(raw: dict[str, pd.Series]) -> tuple[str, float]:
    """
    Determine Inflation direction and confidence from available series.

    raw keys (all optional): CPILFESL, PCEPILFE, T5YIE, DCOILWTICO, CES0500000003.

    Returns (direction, confidence):
        direction  — "Rising" or "Falling"
        confidence — fraction of sub-signals agreeing (0.0–1.0)
    """
    votes: list[int] = []  # +1 = Rising, -1 = Falling

    # Core CPI: rising 3m slope → Rising
    if (cpi := raw.get("CPILFESL")) is not None:
        s = _trailing_slope(cpi, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)

    # Core PCE: rising 3m slope → Rising
    if (pce := raw.get("PCEPILFE")) is not None:
        s = _trailing_slope(pce, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)

    # 5Y Breakeven inflation: rising → Rising
    if (bei := raw.get("T5YIE")) is not None:
        s = _trailing_slope(bei, 5)
        if s is not None:
            votes.append(1 if s > 0 else -1)

    # WTI oil: proxy for commodity-driven inflation pressure
    if (wti := raw.get("DCOILWTICO")) is not None:
        s = _trailing_slope(wti, 5)
        if s is not None:
            votes.append(1 if s > 0 else -1)

    # Wage growth: rising → Rising (inflationary)
    if (wages := raw.get("CES0500000003")) is not None:
        s = _trailing_slope(wages, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)

    if not votes:
        return "Rising", 0.5  # neutral fallback

    rising = sum(1 for v in votes if v == 1)
    falling = len(votes) - rising
    direction = "Rising" if rising >= falling else "Falling"
    dominant = max(rising, falling)
    confidence = dominant / len(votes)
    return direction, round(confidence, 4)
