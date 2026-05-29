from __future__ import annotations

import numpy as np
import pandas as pd


def _percentile(series: pd.Series, current: float) -> float:
    """Return 5-year percentile of `current` within `series` (0–100)."""
    vals = series.dropna().values
    if len(vals) == 0:
        return 50.0
    return float(np.sum(vals <= current) / len(vals) * 100)


def _risk_score(series: pd.Series, current: float, higher_is_riskier: bool) -> float:
    pct = _percentile(series, current)
    return pct if higher_is_riskier else (100.0 - pct)


def compute_amplifier_score(
    raw: dict[str, pd.Series],
    weights: dict[str, float] | None = None,
) -> tuple[float, dict]:
    """
    Compute Risk Amplifier score (0–100) from Liquidity, Credit, Rates sub-scores.

    raw keys (all optional): full historical Series keyed by FRED code or yfinance ticker.
    Each series must cover ~5 years for meaningful percentile normalization.

    Returns (amplifier_score, category_scores dict).
    """
    if weights is None:
        weights = {"liquidity": 0.35, "credit": 0.40, "rates": 0.25}

    def last(key: str) -> float | None:
        s = raw.get(key)
        if s is None:
            return None
        v = s.dropna()
        return float(v.iloc[-1]) if len(v) else None

    def score(key: str, higher_is_riskier: bool) -> float | None:
        s = raw.get(key)
        cur = last(key)
        if s is None or cur is None:
            return None
        return _risk_score(s, cur, higher_is_riskier)

    # ── Liquidity ──────────────────────────────────────────────────────────────
    # WALCL rising = more liquidity = lower risk → lower_is_riskier
    # M2SL rising = more liquidity = lower risk
    # RRPONTSYD high = more cash parked at Fed = tighter conditions = higher risk
    # NFCI: higher = tighter financial conditions = higher risk
    liq_scores = [
        score("WALCL", higher_is_riskier=False),
        score("M2SL", higher_is_riskier=False),
        score("RRPONTSYD", higher_is_riskier=True),
        score("NFCI", higher_is_riskier=True),
    ]
    liq_valid = [s for s in liq_scores if s is not None]
    liq = float(np.mean(liq_valid)) if liq_valid else 50.0

    # ── Credit ────────────────────────────────────────────────────────────────
    # HY Spread high = stress = higher risk
    # IG Spread high = stress = higher risk
    # DRTSCILM: lending standards tightening = higher risk (higher index = tighter)
    cred_scores = [
        score("BAMLH0A0HYM2", higher_is_riskier=True),
        score("BAMLC0A0CM", higher_is_riskier=True),
        score("DRTSCILM", higher_is_riskier=True),
    ]
    cred_valid = [s for s in cred_scores if s is not None]
    cred = float(np.mean(cred_valid)) if cred_valid else 50.0

    # ── Rates ─────────────────────────────────────────────────────────────────
    # Real rate (DFII10) high = tighter = more risk
    # T10Y2Y inverted (low/negative) = recession risk = higher risk
    # EFFR high (relative to history) = tighter = more risk
    rates_scores = [
        score("DFII10", higher_is_riskier=True),
        score("T10Y2Y", higher_is_riskier=False),  # low yield curve = inverted = risk
        score("EFFR", higher_is_riskier=True),
    ]
    rates_valid = [s for s in rates_scores if s is not None]
    rates = float(np.mean(rates_valid)) if rates_valid else 50.0

    amp = (
        weights["liquidity"] * liq
        + weights["credit"] * cred
        + weights["rates"] * rates
    )

    category_scores = {
        "liquidity": round(liq, 2),
        "credit": round(cred, 2),
        "rates": round(rates, 2),
    }
    return round(amp, 2), category_scores
