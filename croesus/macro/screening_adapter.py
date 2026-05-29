from __future__ import annotations

from pathlib import Path

import yaml

from croesus.macro.models import MacroState

_CONFIG_PATH = Path(__file__).with_name("config.yaml")


def _load_config() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


def get_screening_params(state: MacroState) -> dict:
    """
    Convert MacroState into a screening parameter dict.

    Returns:
        factor_weights       — adjusted factor weights (Regime-based)
        filters              — amplifier-adjusted filter multipliers
        candidate_count      — confirmation-adjusted candidate pool size
        positioning          — passthrough from MacroState
        regime               — passthrough from MacroState
    """
    cfg = _load_config()
    scr = cfg["screening"]

    # ── Factor weights (Regime-based) ─────────────────────────────────────────
    weights = dict(scr["base_weights"])
    overrides = scr["regime_overrides"].get(state.regime, {})
    for k, delta in overrides.items():
        weights[k] = round(weights.get(k, 0.0) + delta, 4)

    # ── Filters (Amplifier-based) ─────────────────────────────────────────────
    filters: dict[str, float] = {}
    if state.amplifier_score > scr["amplifier_stress_threshold"]:
        sf = scr["amplifier_stress_filters"]
        filters["min_liquidity_multiplier"] = sf["min_liquidity_multiplier"]
        filters["max_volatility_multiplier"] = sf["max_volatility_multiplier"]
        filters["min_market_cap_multiplier"] = sf["min_market_cap_multiplier"]

    # ── Candidate count (Confirmation-based) ─────────────────────────────────
    base = scr["base_candidate_count"]
    candidate_count = int(base * (1 + state.confirmation_score * 0.3))
    candidate_count = max(5, candidate_count)

    return {
        "factor_weights": weights,
        "filters": filters,
        "candidate_count": candidate_count,
        "positioning": state.positioning,
        "regime": state.regime,
        "amplifier_score": state.amplifier_score,
        "confirmation_score": state.confirmation_score,
    }
