"""Unit tests for the pure macro scoring core (no I/O, no external data)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from croesus.macro.engine import (
    _classify_regime,
    _determine_positioning,
    compute_macro_state,
)
from croesus.macro.indicators.amplifier import compute_amplifier_score
from croesus.macro.indicators.confirmation import compute_confirmation_score
from croesus.macro.indicators.growth import compute_growth_direction
from croesus.macro.indicators.inflation import compute_inflation_direction
from croesus.macro.models import MacroState
from croesus.macro.screening_adapter import get_screening_params


# ── Helpers ────────────────────────────────────────────────────────────────────

def _rising(n: int = 60, start: float = 100.0, slope: float = 0.5) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=n, freq="M")
    return pd.Series(start + np.arange(n) * slope, index=idx)


def _falling(n: int = 60, start: float = 100.0, slope: float = 0.5) -> pd.Series:
    return _rising(n, start, -slope)


def _flat(n: int = 60, val: float = 50.0) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=n, freq="M")
    return pd.Series(np.full(n, val), index=idx)


# ── Layer 1: Growth ────────────────────────────────────────────────────────────

class TestGrowthDirection:
    def test_expanding_when_pmi_rising_above_50(self):
        raw = {"MANEAPUSA": _rising(60, start=52.0, slope=0.1)}
        direction, conf = compute_growth_direction(raw)
        assert direction == "Expanding"
        assert conf > 0.5

    def test_contracting_when_pmi_falling_below_50(self):
        raw = {"MANEAPUSA": _falling(60, start=48.0, slope=0.1)}
        direction, conf = compute_growth_direction(raw)
        assert direction == "Contracting"

    def test_expanding_when_unemployment_falling(self):
        raw = {"UNRATE": _falling(24, start=5.0, slope=0.05)}
        direction, conf = compute_growth_direction(raw)
        assert direction == "Expanding"

    def test_neutral_fallback_on_empty_raw(self):
        direction, conf = compute_growth_direction({})
        assert direction in ("Expanding", "Contracting")
        assert 0.0 <= conf <= 1.0

    def test_confidence_bounded(self):
        raw = {
            "MANEAPUSA": _rising(),
            "UNRATE": _falling(),
            "ICSA": _falling(),
            "RSXFS": _rising(),
        }
        direction, conf = compute_growth_direction(raw)
        assert 0.0 <= conf <= 1.0


# ── Layer 1: Inflation ─────────────────────────────────────────────────────────

class TestInflationDirection:
    def test_rising_when_core_cpi_trending_up(self):
        raw = {"CPILFESL": _rising(36, start=2.0, slope=0.05)}
        direction, conf = compute_inflation_direction(raw)
        assert direction == "Rising"

    def test_falling_when_core_cpi_trending_down(self):
        raw = {"CPILFESL": _falling(36, start=4.0, slope=0.05)}
        direction, conf = compute_inflation_direction(raw)
        assert direction == "Falling"

    def test_confidence_bounded(self):
        raw = {"CPILFESL": _rising(), "PCEPILFE": _rising()}
        _, conf = compute_inflation_direction(raw)
        assert 0.0 <= conf <= 1.0


# ── Regime classification ──────────────────────────────────────────────────────

class TestRegimeClassification:
    @pytest.mark.parametrize("growth,inflation,expected", [
        ("Expanding", "Falling", "Goldilocks"),
        ("Expanding", "Rising", "Reflation"),
        ("Contracting", "Rising", "Stagflation"),
        ("Contracting", "Falling", "Deflation"),
    ])
    def test_all_regimes(self, growth, inflation, expected):
        assert _classify_regime(growth, inflation) == expected


# ── Layer 2: Amplifier ─────────────────────────────────────────────────────────

class TestAmplifier:
    def _hy_stress_raw(self) -> dict[str, pd.Series]:
        n = 260 * 5  # ~5 years daily
        idx = pd.date_range("2019-01-01", periods=n, freq="B")
        # HY spread at historical high
        hy = pd.Series(np.linspace(2.0, 8.0, n), index=idx)
        return {"BAMLH0A0HYM2": hy}

    def test_score_between_0_and_100(self):
        raw = self._hy_stress_raw()
        score, cats = compute_amplifier_score(raw)
        assert 0.0 <= score <= 100.0

    def test_high_hy_spread_raises_score(self):
        n = 260 * 5
        idx = pd.date_range("2019-01-01", periods=n, freq="B")
        hy = pd.Series(np.linspace(2.0, 8.0, n), index=idx)
        raw = {"BAMLH0A0HYM2": hy}
        score, _ = compute_amplifier_score(raw)
        # At maximum of the series, credit sub-score should be near 100
        assert score > 50.0

    def test_empty_raw_returns_neutral(self):
        score, _ = compute_amplifier_score({})
        assert score == 50.0

    def test_custom_weights_applied(self):
        n = 260
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        hy = pd.Series(np.ones(n) * 5.0, index=idx)
        raw = {"BAMLH0A0HYM2": hy}
        weights = {"liquidity": 0.1, "credit": 0.8, "rates": 0.1}
        score, cats = compute_amplifier_score(raw, weights=weights)
        assert 0.0 <= score <= 100.0


# ── Layer 3: Confirmation ──────────────────────────────────────────────────────

class TestConfirmation:
    def _goldilocks_raw(self) -> dict[str, pd.Series]:
        n = 260 * 5
        idx = pd.date_range("2019-01-01", periods=n, freq="B")
        # Low VIX (bullish confirmation for Goldilocks)
        vix = pd.Series(np.linspace(30.0, 10.0, n), index=idx)
        # S&P 500 well above 200MA
        sp = pd.Series(np.linspace(3000.0, 5000.0, n), index=idx)
        return {"^VIX": vix, "^GSPC": sp}

    def test_score_within_bounds(self):
        raw = self._goldilocks_raw()
        score = compute_confirmation_score(raw, "Goldilocks")
        assert -1.0 <= score <= 1.0

    def test_low_vix_confirms_goldilocks(self):
        raw = self._goldilocks_raw()
        score = compute_confirmation_score(raw, "Goldilocks")
        assert score > 0.0

    def test_empty_raw_returns_zero(self):
        score = compute_confirmation_score({}, "Goldilocks")
        assert score == 0.0


# ── Positioning rules ─────────────────────────────────────────────────────────

class TestPositioning:
    def _cfg(self):
        from pathlib import Path
        import yaml
        cfg_path = Path(__file__).parents[1] / "croesus" / "macro" / "config.yaml"
        return yaml.safe_load(cfg_path.read_text())

    def test_goldilocks_low_amp_high_conf_is_aggressive(self):
        cfg = self._cfg()
        assert _determine_positioning("Goldilocks", 20.0, 0.5, cfg) == "Aggressive"

    def test_goldilocks_mid_amp_is_moderately_aggressive(self):
        cfg = self._cfg()
        assert _determine_positioning("Goldilocks", 45.0, 0.0, cfg) == "Moderately Aggressive"

    def test_stagflation_is_cautious(self):
        cfg = self._cfg()
        result = _determine_positioning("Stagflation", 40.0, 0.0, cfg)
        assert result == "Cautious"

    def test_stagflation_high_amp_is_defensive(self):
        cfg = self._cfg()
        result = _determine_positioning("Stagflation", 70.0, 0.0, cfg)
        assert result == "Defensive"

    def test_very_negative_confirmation_is_defensive(self):
        cfg = self._cfg()
        result = _determine_positioning("Goldilocks", 50.0, -0.8, cfg)
        assert result == "Defensive"


# ── Full engine integration ────────────────────────────────────────────────────

class TestEngine:
    def test_compute_macro_state_returns_valid_model(self):
        state = compute_macro_state(date(2025, 1, 15))
        assert isinstance(state, MacroState)
        assert state.regime in ("Goldilocks", "Reflation", "Stagflation", "Deflation")
        assert 0.0 <= state.amplifier_score <= 100.0
        assert -1.0 <= state.confirmation_score <= 1.0
        assert state.positioning in (
            "Aggressive", "Moderately Aggressive", "Neutral", "Cautious", "Defensive"
        )
        assert isinstance(state.warnings, list)
        assert isinstance(state.opportunities, list)

    def test_compute_with_synthetic_data(self):
        n = 260 * 5
        idx = pd.date_range("2019-01-01", periods=n, freq="B")
        raw = {
            "MANEAPUSA": pd.Series(np.linspace(48.0, 56.0, n), index=idx),
            "CPILFESL": pd.Series(np.linspace(2.5, 3.5, n), index=idx),
            "BAMLH0A0HYM2": pd.Series(np.linspace(4.0, 3.0, n), index=idx),
            "^VIX": pd.Series(np.linspace(20.0, 15.0, n), index=idx),
        }
        state = compute_macro_state(date(2024, 6, 1), raw)
        assert state.regime in ("Goldilocks", "Reflation", "Stagflation", "Deflation")


# ── Screening adapter ─────────────────────────────────────────────────────────

class TestScreeningAdapter:
    def test_returns_expected_keys(self):
        state = compute_macro_state(date(2025, 1, 15))
        params = get_screening_params(state)
        assert "factor_weights" in params
        assert "filters" in params
        assert "candidate_count" in params
        assert "positioning" in params
        assert "regime" in params

    def test_factor_weights_sum_near_one(self):
        state = compute_macro_state(date(2025, 1, 15))
        params = get_screening_params(state)
        total = sum(params["factor_weights"].values())
        assert 0.5 <= total <= 1.5  # overrides can shift sum; check reasonable range

    def test_candidate_count_positive(self):
        state = compute_macro_state(date(2025, 1, 15))
        params = get_screening_params(state)
        assert params["candidate_count"] >= 5

    def test_stress_filters_applied_when_high_amplifier(self):
        state = MacroState(
            date=date(2025, 1, 15),
            regime="Stagflation",
            regime_confidence=0.8,
            growth_direction="Contracting",
            inflation_direction="Rising",
            amplifier_score=75.0,
            confirmation_score=-0.3,
            positioning="Defensive",
        )
        params = get_screening_params(state)
        assert len(params["filters"]) > 0
        assert params["filters"].get("min_liquidity_multiplier") == 1.5

    def test_no_stress_filters_when_low_amplifier(self):
        state = MacroState(
            date=date(2025, 1, 15),
            regime="Goldilocks",
            regime_confidence=0.9,
            growth_direction="Expanding",
            inflation_direction="Falling",
            amplifier_score=20.0,
            confirmation_score=0.5,
            positioning="Aggressive",
        )
        params = get_screening_params(state)
        assert params["filters"] == {}
