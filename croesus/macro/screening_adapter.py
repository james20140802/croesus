from __future__ import annotations

from pathlib import Path

import yaml

from croesus.macro.models import MacroState

_CONFIG_PATH = Path(__file__).with_name("config.yaml")

_HORIZON_KEY = "momentum_horizon_weights"


def _load_config() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


def _stress_t(amplifier_score: float) -> float:
    """Map a 0–100 amplifier (stress) score onto the [0, 1] interpolation factor."""
    return max(0.0, min(1.0, amplifier_score / 100.0))


def neutral_screening_params() -> dict:
    """
    Default screening parameters used when no MacroState is available
    (e.g. daily_macro_run has not run yet). Base weights from config,
    base candidate count, no stress filters, regime unknown.
    """
    cfg = _load_config()
    scr = cfg["screening"]
    return {
        "factor_weights": dict(scr["base_weights"]),
        "momentum_horizon_weights": dict(scr.get("base_momentum_horizon_weights") or {}),
        "momentum_scaling": scr.get("momentum_scaling", "raw"),
        "trend_gate_postures": list(scr.get("trend_gate_postures") or []),
        "interpolation": scr.get("interpolation", "discrete"),
        "filters": {},
        "candidate_count": scr["base_candidate_count"],
        "positioning": None,
        "regime": None,
        "amplifier_score": None,
        "confirmation_score": None,
    }


def get_screening_params(state: MacroState) -> dict:
    """
    Convert MacroState into a screening parameter dict.

    Returns:
        factor_weights            — adjusted factor weights (Regime-based)
        momentum_horizon_weights  — per-horizon momentum weights (Regime-based)
        momentum_scaling          — raw | vol_scaled (config passthrough)
        trend_gate_postures       — postures that gate on the 200d MA
        interpolation             — discrete | continuous (config passthrough)
        filters                   — amplifier-adjusted filter multipliers
        candidate_count           — confirmation-adjusted candidate pool size
        positioning               — passthrough from MacroState
        regime                    — passthrough from MacroState
    """
    cfg = _load_config()
    scr = cfg["screening"]
    interpolation = scr.get("interpolation", "discrete")
    t = _stress_t(state.amplifier_score)

    overrides = scr["regime_overrides"].get(state.regime, {})

    # ── Factor weights (Regime-based) ─────────────────────────────────────────
    weights = _interpolate_weights(
        dict(scr["base_weights"]),
        {k: v for k, v in overrides.items() if k != _HORIZON_KEY},
        interpolation,
        t,
    )

    # ── Momentum horizon weights (Regime-based) ───────────────────────────────
    horizon_weights = _interpolate_weights(
        dict(scr.get("base_momentum_horizon_weights") or {}),
        dict(overrides.get(_HORIZON_KEY) or {}),
        interpolation,
        t,
        as_target=True,
    )

    # ── Filters (Amplifier-based) ─────────────────────────────────────────────
    filters = _stress_filters(scr, interpolation, t, state.amplifier_score)

    # ── Candidate count (Confirmation-based) ─────────────────────────────────
    base = scr["base_candidate_count"]
    candidate_count = int(base * (1 + state.confirmation_score * 0.3))
    candidate_count = max(5, candidate_count)

    return {
        "factor_weights": weights,
        "momentum_horizon_weights": horizon_weights,
        "momentum_scaling": scr.get("momentum_scaling", "raw"),
        "trend_gate_postures": list(scr.get("trend_gate_postures") or []),
        "interpolation": interpolation,
        "filters": filters,
        "candidate_count": candidate_count,
        "positioning": state.positioning,
        "regime": state.regime,
        "amplifier_score": state.amplifier_score,
        "confirmation_score": state.confirmation_score,
    }


def _interpolate_weights(
    base: dict[str, float],
    overrides: dict[str, float],
    interpolation: str,
    t: float,
    *,
    as_target: bool = False,
) -> dict[str, float]:
    """
    Blend ``base`` with regime ``overrides``.

    With ``as_target=False`` overrides are additive deltas (factor weights);
    with ``as_target=True`` overrides are absolute target values (horizon
    weights). In both cases ``discrete`` applies the override at full strength
    and ``continuous`` interpolates by ``t`` so ``t = 0`` is ``base`` and
    ``t = 1`` reproduces the discrete result.
    """
    if not overrides:
        return dict(base)
    blended: dict[str, float] = {}
    for key in set(base) | set(overrides):
        base_value = base.get(key, 0.0)
        if as_target:
            target = overrides.get(key, base_value)
            delta = target - base_value
        else:
            delta = overrides.get(key, 0.0)
        factor = t if interpolation == "continuous" else 1.0
        blended[key] = round(base_value + factor * delta, 4)
    return blended


def _stress_filters(
    scr: dict,
    interpolation: str,
    t: float,
    amplifier_score: float,
) -> dict[str, float]:
    sf = scr["amplifier_stress_filters"]
    keys = (
        "min_liquidity_multiplier",
        "max_volatility_multiplier",
        "min_market_cap_multiplier",
    )
    if interpolation == "continuous":
        # Interpolate from a no-op multiplier of 1.0 up to the configured
        # stress value so filters tighten monotonically as stress rises.
        return {key: round(1.0 + t * (sf[key] - 1.0), 4) for key in keys}
    if amplifier_score > scr["amplifier_stress_threshold"]:
        return {key: sf[key] for key in keys}
    return {}
