from __future__ import annotations

import numpy as np
import pandas as pd


def _percentile(series: pd.Series, current: float) -> float:
    vals = series.dropna().values
    if len(vals) == 0:
        return 50.0
    return float(np.sum(vals <= current) / len(vals) * 100)


def compute_confirmation_score(
    raw: dict[str, pd.Series],
    regime: str,
) -> float:
    """
    Compute Confirmation score (-1.0 to +1.0) indicating whether market
    indicators agree with the classified regime.

    A score near +1.0 means all signals confirm the regime; near -1.0 means divergence.
    """
    sub_scores: list[float] = []

    def last(key: str) -> float | None:
        s = raw.get(key)
        if s is None:
            return None
        v = s.dropna()
        return float(v.iloc[-1]) if len(v) else None

    def pct(key: str) -> float | None:
        s = raw.get(key)
        cur = last(key)
        if s is None or cur is None:
            return None
        return _percentile(s, cur)

    # ── Volatility ────────────────────────────────────────────────────────────
    # Goldilocks/Reflation: low VIX confirms; high VIX warns
    vix_pct = pct("^VIX")
    if vix_pct is not None:
        if regime in ("Goldilocks", "Reflation"):
            sub_scores.append(1.0 - vix_pct / 50.0)   # <50th pct → positive
        else:
            sub_scores.append(vix_pct / 50.0 - 1.0)   # high VIX confirms stress

    # VIX3M/VIX term structure: ratio > 1 = backwardation = calm (confirms Goldilocks)
    vix_cur = last("^VIX")
    vix3m_cur = last("^VIX3M")
    if vix_cur is not None and vix3m_cur is not None and vix_cur > 0:
        ratio = vix3m_cur / vix_cur
        if regime in ("Goldilocks",):
            sub_scores.append(min(1.0, (ratio - 1.0) * 2))
        elif regime in ("Stagflation", "Deflation"):
            sub_scores.append(min(1.0, (1.0 - ratio) * 2))

    # ── Market Trend ──────────────────────────────────────────────────────────
    # S&P 500 above 200-day MA confirms risk-on (Goldilocks/Reflation)
    sp500 = raw.get("^GSPC")
    if sp500 is not None and len(sp500.dropna()) >= 200:
        vals = sp500.dropna()
        ma200 = float(vals.tail(200).mean())
        cur_price = float(vals.iloc[-1])
        above = cur_price > ma200
        if regime in ("Goldilocks", "Reflation"):
            sub_scores.append(1.0 if above else -1.0)
        else:
            sub_scores.append(-1.0 if above else 1.0)

    # ── Sentiment ─────────────────────────────────────────────────────────────
    # AAII Bull-Bear spread: extreme bullishness in Goldilocks = contrarian warning
    aaii = raw.get("aaii_bull_bear")
    aaii_pct = pct("aaii_bull_bear")
    if aaii_pct is not None:
        if regime in ("Goldilocks",):
            # Mildly positive confirms; extreme bullishness (>80th) is contrarian warning
            sub_scores.append(1.0 - aaii_pct / 50.0 if aaii_pct <= 80 else -0.5)
        elif regime in ("Stagflation", "Deflation"):
            sub_scores.append(aaii_pct / 50.0 - 1.0)

    # NAAIM: high exposure in Goldilocks is slightly bullish; extreme = warning
    naaim = raw.get("naaim_exposure")
    naaim_pct = pct("naaim_exposure")
    if naaim_pct is not None:
        if regime in ("Goldilocks",):
            sub_scores.append(naaim_pct / 100.0 - 0.3)
        elif regime in ("Stagflation", "Deflation"):
            sub_scores.append(0.3 - naaim_pct / 100.0)

    # ── FX & Commodities ──────────────────────────────────────────────────────
    # Copper/Gold ratio: rising confirms Goldilocks/Reflation (growth signal)
    hg = last("HG=F")
    gc = last("GC=F")
    if hg is not None and gc is not None and gc > 0:
        cg_ratio = hg / gc
        cg_series = None
        hg_s = raw.get("HG=F")
        gc_s = raw.get("GC=F")
        if hg_s is not None and gc_s is not None:
            aligned = pd.concat({"hg": hg_s, "gc": gc_s}, axis=1).dropna()
            if len(aligned) > 0:
                cg_series_raw = aligned["hg"] / aligned["gc"]
                cg_pct = _percentile(cg_series_raw, cg_ratio)
                if regime in ("Goldilocks", "Reflation"):
                    sub_scores.append(cg_pct / 50.0 - 1.0)
                else:
                    sub_scores.append(1.0 - cg_pct / 50.0)

    # DXY: strong dollar (high pct) is headwind for risk assets
    dxy_pct = pct("DX-Y.NYB")
    if dxy_pct is not None:
        if regime in ("Goldilocks",):
            sub_scores.append(1.0 - dxy_pct / 50.0)
        elif regime in ("Stagflation", "Deflation"):
            sub_scores.append(dxy_pct / 50.0 - 1.0)

    if not sub_scores:
        return 0.0

    score = float(np.mean(sub_scores))
    return round(max(-1.0, min(1.0, score)), 4)
