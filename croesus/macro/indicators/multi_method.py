from __future__ import annotations

"""
Alternative regime classification methods for cross-validation.

These are reference implementations of approaches used by major institutions:
  - blackrock:    BlackRock — 3M vs 6M moving average crossover (direction momentum)
  - level:        Generic — absolute level thresholds (PMI≥50, CPI YoY≥3%)
  - aqr_momentum: AQR — 1-year change in indicator levels (yearly momentum)

The primary regime used for screening is always the ensemble vote in engine.py.
These methods are output-only reference signals — never wired into screening_adapter.
"""

import pandas as pd


def _regime(growth: str, inflation: str) -> str:
    if growth == "Expanding" and inflation == "Falling":
        return "Goldilocks"
    if growth == "Expanding" and inflation == "Rising":
        return "Reflation"
    if growth == "Contracting" and inflation == "Rising":
        return "Stagflation"
    return "Deflation"


def _best_activity_series(raw: dict[str, pd.Series]) -> pd.Series | None:
    """
    Return the best available activity/PMI proxy in priority order:
      ism_mfg_pmi (scraped, 30-70 range)  →
      MANEAPUSA   (FRED, removed 2016, likely empty)  →
      CFNAI       (Chicago Fed NAI, -3 to +3 z-score range)
    """
    for key in ("ism_mfg_pmi", "MANEAPUSA", "CFNAI"):
        s = raw.get(key)
        if s is not None:
            s = s.dropna()
            if len(s) >= 6:
                return s
    return None


def _cpi_yoy(raw: dict[str, pd.Series]) -> pd.Series | None:
    """Compute CPI YoY % from first available level series (CPILFESL or PCEPILFE)."""
    for key in ("CPILFESL", "PCEPILFE"):
        s = raw.get(key)
        if s is not None:
            yoy = s.dropna().pct_change(12) * 100
            yoy = yoy.dropna()
            if len(yoy) >= 6:
                return yoy
    return None


# ── BlackRock method ──────────────────────────────────────────────────────────

def blackrock_method(raw: dict[str, pd.Series]) -> dict:
    """
    BlackRock: sign(3M moving average − 6M moving average).

    Growth: applied to PMI or CFNAI.  Positive crossover = Expanding.
    Inflation: applied to CPI YoY.    Positive crossover = Rising.

    Captures acceleration/deceleration rather than absolute level.
    Source: BlackRock Investment Institute published framework.
    """
    growth_dir, growth_conf = "Expanding", 0.5
    activity = _best_activity_series(raw)
    if activity is not None and len(activity) >= 6:
        short = float(activity.tail(3).mean())
        long_ = float(activity.tail(6).mean())
        diff = short - long_
        std = float(activity.tail(6).std()) or 1.0
        growth_dir = "Expanding" if diff > 0 else "Contracting"
        growth_conf = round(min(abs(diff) / std, 1.0), 4)

    # Blend services PMI into growth if available (manufacturing + services average)
    svc = raw.get("ism_svc_pmi")
    if svc is not None:
        svc = svc.dropna()
        if len(svc) >= 6:
            svc_diff = float(svc.tail(3).mean()) - float(svc.tail(6).mean())
            base_diff = (activity.tail(3).mean() - activity.tail(6).mean()) if activity is not None else svc_diff
            combined = (float(base_diff) + svc_diff) / 2
            growth_dir = "Expanding" if combined > 0 else "Contracting"

    infl_dir, infl_conf = "Rising", 0.5
    yoy = _cpi_yoy(raw)
    if yoy is not None and len(yoy) >= 6:
        short = float(yoy.tail(3).mean())
        long_ = float(yoy.tail(6).mean())
        diff = short - long_
        std = float(yoy.tail(6).std()) or 0.5
        infl_dir = "Rising" if diff > 0 else "Falling"
        infl_conf = round(min(abs(diff) / std, 1.0), 4)

    return {
        "growth": growth_dir,
        "inflation": infl_dir,
        "regime": _regime(growth_dir, infl_dir),
        "confidence": round((growth_conf + infl_conf) / 2, 4),
        "type": "direction_momentum",
        "description": "BlackRock: 3M vs 6M moving average (acceleration signal)",
    }


# ── Level threshold method ────────────────────────────────────────────────────

def level_method(raw: dict[str, pd.Series]) -> dict:
    """
    Absolute level thresholds — classic practitioner heuristics.

    Growth:    PMI ≥ 50 = Expanding (ISM convention)
               CFNAI ≥ 0 = Expanding (above-trend convention) if no PMI
    Inflation: Core CPI YoY ≥ 3.0% = Rising

    Simple and interpretable; does not capture acceleration.
    """
    growth_signals: list[int] = []

    # ISM PMI: 50 is the expansion/contraction boundary
    for key in ("ism_mfg_pmi", "MANEAPUSA"):
        s = raw.get(key)
        if s is not None:
            v = s.dropna()
            if len(v):
                growth_signals.append(1 if float(v.iloc[-1]) >= 50.0 else -1)

    if (svc := raw.get("ism_svc_pmi")) is not None:
        v = svc.dropna()
        if len(v):
            growth_signals.append(1 if float(v.iloc[-1]) >= 50.0 else -1)

    # Fall back to CFNAI if no PMI available (CFNAI ≥ 0 = above-trend)
    if not growth_signals:
        cfnai = raw.get("CFNAI")
        if cfnai is not None:
            v = cfnai.dropna()
            if len(v):
                growth_signals.append(1 if float(v.iloc[-1]) >= 0.0 else -1)

    if growth_signals:
        score = sum(growth_signals)
        growth_dir = "Expanding" if score >= 0 else "Contracting"
        growth_conf = round(abs(score) / len(growth_signals), 4)
    else:
        growth_dir, growth_conf = "Expanding", 0.5

    # CPI YoY level vs 3% threshold (Fed's informal "uncomfortable" line)
    infl_dir, infl_conf = "Rising", 0.5
    yoy = _cpi_yoy(raw)
    if yoy is not None and len(yoy):
        level = float(yoy.iloc[-1])
        infl_dir = "Rising" if level >= 3.0 else "Falling"
        # Confidence scales with distance from threshold (2pp away = full confidence)
        infl_conf = round(min(abs(level - 3.0) / 2.0, 1.0), 4)

    return {
        "growth": growth_dir,
        "inflation": infl_dir,
        "regime": _regime(growth_dir, infl_dir),
        "confidence": round((growth_conf + infl_conf) / 2, 4),
        "type": "level",
        "description": "Level Threshold: PMI ≥ 50, CPI YoY ≥ 3.0%",
    }


# ── AQR 1-year momentum method ────────────────────────────────────────────────

def aqr_momentum_method(raw: dict[str, pd.Series]) -> dict:
    """
    AQR-style 1-year momentum: current level vs. 12 months ago.

    Based on "A Half Century of Macro Momentum" (AQR, Brooks 2017).
    Uses changes in indicator levels to capture markets' underreaction to
    sustained macro trends. Positive 1-year change = favorable direction.
    """
    growth_dir, growth_conf = "Expanding", 0.5
    activity = _best_activity_series(raw)
    if activity is not None and len(activity) >= 13:
        change = float(activity.iloc[-1]) - float(activity.iloc[-13])
        std = float(activity.std()) or 1.0
        growth_dir = "Expanding" if change > 0 else "Contracting"
        growth_conf = round(min(abs(change) / std, 1.0), 4)

    infl_dir, infl_conf = "Rising", 0.5
    yoy = _cpi_yoy(raw)
    if yoy is not None and len(yoy) >= 13:
        change = float(yoy.iloc[-1]) - float(yoy.iloc[-13])
        std = float(yoy.std()) or 0.5
        infl_dir = "Rising" if change > 0 else "Falling"
        infl_conf = round(min(abs(change) / std, 1.0), 4)

    return {
        "growth": growth_dir,
        "inflation": infl_dir,
        "regime": _regime(growth_dir, infl_dir),
        "confidence": round((growth_conf + infl_conf) / 2, 4),
        "type": "yearly_momentum",
        "description": "AQR Momentum: 1-year change in indicator levels",
    }


# ── Aggregator ────────────────────────────────────────────────────────────────

def get_all_methods(raw: dict[str, pd.Series]) -> dict[str, dict]:
    """Run all three alternative classification methods. Returns keyed dict."""
    return {
        "blackrock": blackrock_method(raw),
        "level": level_method(raw),
        "aqr_momentum": aqr_momentum_method(raw),
    }
