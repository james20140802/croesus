from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from croesus.macro.indicators.amplifier import compute_amplifier_score
from croesus.macro.indicators.confirmation import compute_confirmation_score
from croesus.macro.indicators.growth import compute_growth_direction
from croesus.macro.indicators.inflation import compute_inflation_direction
from croesus.macro.indicators.multi_method import get_all_methods
from croesus.macro.models import MacroState
from croesus.macro.templates import generate_opportunities, generate_warnings

_CONFIG_PATH = Path(__file__).with_name("config.yaml")


def _load_config() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


def _classify_regime(growth: str, inflation: str) -> str:
    if growth == "Expanding" and inflation == "Falling":
        return "Goldilocks"
    if growth == "Expanding" and inflation == "Rising":
        return "Reflation"
    if growth == "Contracting" and inflation == "Rising":
        return "Stagflation"
    return "Deflation"


def _determine_positioning(
    regime: str,
    amplifier: float,
    confirmation: float,
    cfg: dict,
) -> str:
    """
    Evaluate positioning rules from config in order; return first match.
    """
    for rule in cfg["positioning_thresholds"]:
        cond = rule["condition"]
        if not cond:
            return rule["positioning"]

        if "regime" in cond and cond["regime"] != regime:
            continue
        if "amplifier_max" in cond and amplifier > cond["amplifier_max"]:
            continue
        if "amplifier_min" in cond and amplifier < cond["amplifier_min"]:
            continue
        if "confirmation_min" in cond and confirmation < cond["confirmation_min"]:
            continue
        if "confirmation_max" in cond and confirmation > cond["confirmation_max"]:
            continue
        return rule["positioning"]

    return "Neutral"


def compute_macro_state(
    as_of: date,
    raw: dict[str, pd.Series] | None = None,
) -> MacroState:
    """
    Compute MacroState for `as_of` date.

    `raw` is a dict mapping FRED codes / yfinance tickers to their historical
    time series (covering ~5 years for meaningful percentile normalization).
    If None or empty, a neutral fallback state is returned.
    """
    cfg = _load_config()

    if not raw:
        raw = {}

    # Layer 1
    growth_dir, growth_conf = compute_growth_direction(raw)
    inflation_dir, inflation_conf = compute_inflation_direction(raw)
    regime = _classify_regime(growth_dir, inflation_dir)
    regime_confidence = round((growth_conf + inflation_conf) / 2.0, 4)

    # Layer 2
    amp_score, category_scores = compute_amplifier_score(
        raw,
        weights=cfg["amplifier"]["category_weights"],
    )

    # Layer 3
    conf_score = compute_confirmation_score(raw, regime)

    # Positioning
    positioning = _determine_positioning(regime, amp_score, conf_score, cfg)

    # Raw indicator snapshot: last value of each series + amplifier category sub-scores
    raw_snapshot: dict = {}
    for key, series in raw.items():
        vals = series.dropna()
        if len(vals):
            raw_snapshot[key] = round(float(vals.iloc[-1]), 6)
    for k, v in category_scores.items():
        raw_snapshot[f"amp_{k}"] = v

    # Compute percentile rankings so templates.py warning/opportunity checks can fire.
    # generate_warnings/generate_opportunities look for e.g. "BAMLH0A0HYM2_pct" keys.
    _PCT_KEYS = ["BAMLH0A0HYM2", "^VIX", "T10Y2Y", "NFCI"]
    for _key in _PCT_KEYS:
        _series = raw.get(_key)
        _cur = raw_snapshot.get(_key)
        if _series is not None and _cur is not None:
            _vals = _series.dropna().values
            if len(_vals):
                raw_snapshot[f"{_key}_pct"] = round(
                    float(np.sum(_vals <= _cur) / len(_vals) * 100), 2
                )

    # Copper/gold ratio and its percentile for STRONG_GROWTH_SIGNAL opportunity.
    _hg_s = raw.get("HG=F")
    _gc_s = raw.get("GC=F")
    if _hg_s is not None and _gc_s is not None:
        _aligned = pd.concat({"hg": _hg_s, "gc": _gc_s}, axis=1).dropna()
        if len(_aligned) > 0:
            _cg_series = _aligned["hg"] / _aligned["gc"]
            _hg_cur = raw_snapshot.get("HG=F")
            _gc_cur = raw_snapshot.get("GC=F")
            if _hg_cur is not None and _gc_cur is not None and _gc_cur > 0:
                _cg_cur = _hg_cur / _gc_cur
                raw_snapshot["copper_gold_ratio"] = round(_cg_cur, 6)
                raw_snapshot["copper_gold_ratio_pct"] = round(
                    float(np.sum(_cg_series.values <= _cg_cur) / len(_cg_series) * 100), 2
                )

    warnings = generate_warnings(raw_snapshot)
    opportunities = generate_opportunities(raw_snapshot)

    # Regime cross-validation: run 3 alternative methods alongside the primary vote
    alt_methods = get_all_methods(raw)
    regime_methods: dict = {
        "vote": {
            "growth": growth_dir,
            "inflation": inflation_dir,
            "regime": regime,
            "confidence": regime_confidence,
            "type": "ensemble_vote",
            "description": "Ensemble Vote: majority across all available indicators",
        },
        **alt_methods,
    }

    return MacroState(
        date=as_of,
        regime=regime,
        regime_confidence=regime_confidence,
        growth_direction=growth_dir,
        inflation_direction=inflation_dir,
        amplifier_score=amp_score,
        confirmation_score=conf_score,
        positioning=positioning,
        warnings=warnings,
        opportunities=opportunities,
        raw_indicators=raw_snapshot,
        regime_methods=regime_methods,
    )
