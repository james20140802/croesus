from __future__ import annotations

import numpy as np
import pandas as pd


def _trailing_slope(series: pd.Series, window: int = 3) -> float | None:
    """Return the linear regression slope over the last `window` non-null values."""
    vals = series.dropna().tail(window)
    if len(vals) < 2:
        return None
    x = np.arange(len(vals), dtype=float)
    slope = float(np.polyfit(x, vals.values, 1)[0])
    return slope


def compute_growth_direction(raw: dict[str, pd.Series]) -> tuple[str, float]:
    """
    Determine Growth direction and confidence from available series.

    raw keys (all optional): ism_mfg_pmi, ism_svc_pmi, CFNAI,
    MANEAPUSA (FRED-removed 2016), UNRATE, ICSA, RSXFS, INDPRO, GDPC1.

    Returns (direction, confidence):
        direction  — "Expanding" or "Contracting"
        confidence — fraction of sub-signals agreeing with the dominant direction (0.0–1.0)
    """
    votes: list[int] = []  # +1 = Expanding, -1 = Contracting

    # ISM Manufacturing PMI (from ism_scraper, 30-70 range)
    # slope vote + level vote (≥50 = expansionary)
    if (mfg_pmi := raw.get("ism_mfg_pmi")) is not None:
        s = _trailing_slope(mfg_pmi, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)
        last = mfg_pmi.dropna()
        if len(last):
            votes.append(1 if float(last.iloc[-1]) >= 50 else -1)
    elif (pmi := raw.get("MANEAPUSA")) is not None:
        # MANEAPUSA: removed from FRED in 2016, kept as fallback
        s = _trailing_slope(pmi, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)
        last = pmi.dropna()
        if len(last):
            votes.append(1 if float(last.iloc[-1]) >= 50 else -1)

    # ISM Services PMI (from ism_scraper) — adds services-sector signal
    if (svc_pmi := raw.get("ism_svc_pmi")) is not None:
        s = _trailing_slope(svc_pmi, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)
        last = svc_pmi.dropna()
        if len(last):
            votes.append(1 if float(last.iloc[-1]) >= 50 else -1)

    # Chicago Fed NAI (CFNAI): composite of 85 indicators; level ≥ 0 = above-trend
    if (cfnai := raw.get("CFNAI")) is not None:
        s = _trailing_slope(cfnai, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)
        last = cfnai.dropna()
        if len(last):
            votes.append(1 if float(last.iloc[-1]) >= 0 else -1)

    # Unemployment: falling → Expanding
    if (unrate := raw.get("UNRATE")) is not None:
        s = _trailing_slope(unrate, 3)
        if s is not None:
            votes.append(1 if s < 0 else -1)

    # Jobless claims: falling → Expanding
    if (icsa := raw.get("ICSA")) is not None:
        s = _trailing_slope(icsa, 4)
        if s is not None:
            votes.append(1 if s < 0 else -1)

    # Retail sales: rising slope → Expanding
    if (rsxfs := raw.get("RSXFS")) is not None:
        s = _trailing_slope(rsxfs, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)

    # Industrial production: rising slope → Expanding
    if (indpro := raw.get("INDPRO")) is not None:
        s = _trailing_slope(indpro, 3)
        if s is not None:
            votes.append(1 if s > 0 else -1)

    # GDP QoQ: last reading positive → Expanding
    if (gdp := raw.get("GDPC1")) is not None:
        vals = gdp.dropna()
        if len(vals) >= 2:
            qoq = vals.pct_change().iloc[-1]
            votes.append(1 if qoq > 0 else -1)

    if not votes:
        return "Expanding", 0.5  # neutral fallback

    expanding = sum(1 for v in votes if v == 1)
    contracting = len(votes) - expanding
    direction = "Expanding" if expanding >= contracting else "Contracting"
    dominant = max(expanding, contracting)
    confidence = dominant / len(votes)
    return direction, round(confidence, 4)
