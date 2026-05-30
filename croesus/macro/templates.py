from __future__ import annotations


def _warning(indicator: str, current: float, percentile: float, code: str) -> dict:
    return {"indicator": indicator, "current": current, "percentile": percentile, "code": code}


def _opportunity(indicator: str, current: float, percentile: float, code: str) -> dict:
    return {"indicator": indicator, "current": current, "percentile": percentile, "code": code}


def generate_warnings(raw: dict) -> list[dict]:
    """Return rule-based warnings from raw indicator values and their percentiles."""
    out: list[dict] = []

    hy = raw.get("BAMLH0A0HYM2")
    hy_pct = raw.get("BAMLH0A0HYM2_pct")
    if hy is not None and hy_pct is not None and hy_pct > 70:
        out.append(_warning("HY Credit Spread", hy, hy_pct, "HIGH_HY_SPREAD"))

    vix = raw.get("^VIX")
    vix_pct = raw.get("^VIX_pct")
    if vix is not None and vix_pct is not None and vix_pct > 75:
        out.append(_warning("VIX", vix, vix_pct, "HIGH_VIX"))

    t10y2y = raw.get("T10Y2Y")
    if t10y2y is not None and t10y2y < 0:
        out.append(_warning("Yield Curve (10Y-2Y)", t10y2y, raw.get("T10Y2Y_pct", 0.0), "INVERTED_YIELD_CURVE"))

    nfci = raw.get("NFCI")
    nfci_pct = raw.get("NFCI_pct")
    if nfci is not None and nfci_pct is not None and nfci_pct > 70:
        out.append(_warning("NFCI", nfci, nfci_pct, "TIGHT_FINANCIAL_CONDITIONS"))

    return out


def generate_opportunities(raw: dict) -> list[dict]:
    """Return rule-based opportunities from raw indicator values and their percentiles."""
    out: list[dict] = []

    hy = raw.get("BAMLH0A0HYM2")
    hy_pct = raw.get("BAMLH0A0HYM2_pct")
    if hy is not None and hy_pct is not None and hy_pct < 20:
        out.append(_opportunity("HY Credit Spread", hy, hy_pct, "TIGHT_CREDIT_SPREADS"))

    vix = raw.get("^VIX")
    vix_pct = raw.get("^VIX_pct")
    if vix is not None and vix_pct is not None and vix_pct < 20:
        out.append(_opportunity("VIX", vix, vix_pct, "LOW_VOLATILITY"))

    copper_gold = raw.get("copper_gold_ratio")
    copper_gold_pct = raw.get("copper_gold_ratio_pct")
    if copper_gold is not None and copper_gold_pct is not None and copper_gold_pct > 70:
        out.append(_opportunity("Copper/Gold Ratio", copper_gold, copper_gold_pct, "STRONG_GROWTH_SIGNAL"))

    return out
