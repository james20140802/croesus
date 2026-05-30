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
from croesus.macro.indicators.multi_method import (
    aqr_momentum_method,
    blackrock_method,
    get_all_methods,
    level_method,
)
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


# ── Multi-method regime classification ────────────────────────────────────────

class TestMultiMethod:
    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _pmi_expanding(n: int = 60) -> pd.Series:
        """PMI series firmly in expansion territory (>50, rising)."""
        idx = pd.date_range("2020-01-01", periods=n, freq="M")
        return pd.Series(52.0 + np.arange(n) * 0.05, index=idx)

    @staticmethod
    def _pmi_contracting(n: int = 60) -> pd.Series:
        """PMI series firmly in contraction territory (<50, falling)."""
        idx = pd.date_range("2020-01-01", periods=n, freq="M")
        return pd.Series(48.0 - np.arange(n) * 0.05, index=idx)

    @staticmethod
    def _cfnai_above_zero(n: int = 60) -> pd.Series:
        """CFNAI rising from positive base (above-trend growth)."""
        idx = pd.date_range("2020-01-01", periods=n, freq="M")
        return pd.Series(0.2 + np.arange(n) * 0.01, index=idx)

    @staticmethod
    def _cfnai_below_zero(n: int = 60) -> pd.Series:
        """CFNAI declining through negative territory."""
        idx = pd.date_range("2020-01-01", periods=n, freq="M")
        return pd.Series(-0.1 - np.arange(n) * 0.01, index=idx)

    @staticmethod
    def _cpi_low(n: int = 60) -> pd.Series:
        """CPI level rising slowly (<3% YoY after warm-up)."""
        idx = pd.date_range("2020-01-01", periods=n, freq="M")
        return pd.Series(100.0 + np.arange(n) * 0.15, index=idx)

    @staticmethod
    def _cpi_high(n: int = 60) -> pd.Series:
        """CPI level rising fast (>3% YoY after warm-up)."""
        idx = pd.date_range("2020-01-01", periods=n, freq="M")
        return pd.Series(100.0 + np.arange(n) * 0.35, index=idx)

    # ── BlackRock method ──────────────────────────────────────────────────────

    def test_blackrock_expanding_when_cfnai_accelerating(self):
        raw = {"CFNAI": self._cfnai_above_zero()}
        result = blackrock_method(raw)
        # Rising CFNAI: 3M avg > 6M avg → Expanding
        assert result["growth"] == "Expanding"
        assert result["type"] == "direction_momentum"
        assert "regime" in result
        assert 0.0 <= result["confidence"] <= 1.0

    def test_blackrock_contracting_when_cfnai_decelerating(self):
        raw = {"CFNAI": self._cfnai_below_zero()}
        result = blackrock_method(raw)
        # Falling CFNAI: 3M avg < 6M avg → Contracting
        assert result["growth"] == "Contracting"

    def test_blackrock_uses_ism_pmi_over_cfnai(self):
        # When ism_mfg_pmi is present it takes priority over CFNAI
        raw = {
            "ism_mfg_pmi": self._pmi_expanding(),
            "CFNAI": self._cfnai_below_zero(),  # contradicts PMI
        }
        result = blackrock_method(raw)
        assert result["growth"] == "Expanding"

    def test_blackrock_inflation_falling_when_cpi_decelerating(self):
        # CPI level falling → YoY: 3M avg < 6M avg → Falling
        raw = {"CFNAI": self._cfnai_above_zero(), "CPILFESL": _falling(60, start=110.0, slope=0.1)}
        result = blackrock_method(raw)
        assert result["inflation"] == "Falling"

    # ── Level threshold method ────────────────────────────────────────────────

    def test_level_expanding_when_pmi_above_50(self):
        raw = {"ism_mfg_pmi": self._pmi_expanding()}
        result = level_method(raw)
        assert result["growth"] == "Expanding"
        assert result["type"] == "level"

    def test_level_contracting_when_pmi_below_50(self):
        raw = {"ism_mfg_pmi": self._pmi_contracting()}
        result = level_method(raw)
        assert result["growth"] == "Contracting"

    def test_level_cfnai_fallback_above_zero(self):
        # No PMI available → falls back to CFNAI ≥ 0
        raw = {"CFNAI": self._cfnai_above_zero()}
        result = level_method(raw)
        assert result["growth"] == "Expanding"

    def test_level_cfnai_fallback_below_zero(self):
        raw = {"CFNAI": self._cfnai_below_zero()}
        result = level_method(raw)
        assert result["growth"] == "Contracting"

    def test_level_inflation_rising_when_cpi_above_3pct(self):
        # slope=0.35/month → YoY ≈ 4.2% after warm-up
        raw = {"CFNAI": self._cfnai_above_zero(), "CPILFESL": self._cpi_high()}
        result = level_method(raw)
        assert result["inflation"] == "Rising"

    def test_level_inflation_falling_when_cpi_below_3pct(self):
        # slope=0.15/month → YoY ≈ 1.8% after warm-up
        raw = {"CFNAI": self._cfnai_above_zero(), "CPILFESL": self._cpi_low()}
        result = level_method(raw)
        assert result["inflation"] == "Falling"

    # ── AQR momentum method ───────────────────────────────────────────────────

    def test_aqr_expanding_when_cfnai_up_from_year_ago(self):
        raw = {"CFNAI": self._cfnai_above_zero()}
        result = aqr_momentum_method(raw)
        # Rising CFNAI: current > 12 months ago → Expanding
        assert result["growth"] == "Expanding"
        assert result["type"] == "yearly_momentum"

    def test_aqr_contracting_when_cfnai_down_from_year_ago(self):
        raw = {"CFNAI": self._cfnai_below_zero()}
        result = aqr_momentum_method(raw)
        assert result["growth"] == "Contracting"

    def test_aqr_uses_pmi_over_cfnai(self):
        raw = {
            "ism_mfg_pmi": self._pmi_contracting(),
            "CFNAI": self._cfnai_above_zero(),  # contradicts PMI
        }
        result = aqr_momentum_method(raw)
        assert result["growth"] == "Contracting"

    def test_aqr_insufficient_data_returns_fallback(self):
        # Only 6 observations — not enough for 1-year comparison
        idx = pd.date_range("2024-01-01", periods=6, freq="M")
        raw = {"CFNAI": pd.Series([0.1] * 6, index=idx)}
        result = aqr_momentum_method(raw)
        assert result["growth"] in ("Expanding", "Contracting")
        assert result["confidence"] == 0.5  # fallback confidence

    # ── get_all_methods aggregator ────────────────────────────────────────────

    def test_get_all_methods_returns_three_keys(self):
        raw = {"CFNAI": self._cfnai_above_zero()}
        methods = get_all_methods(raw)
        assert set(methods.keys()) == {"blackrock", "level", "aqr_momentum"}

    def test_all_methods_have_required_fields(self):
        raw = {"CFNAI": self._cfnai_above_zero(), "CPILFESL": self._cpi_low()}
        for name, m in get_all_methods(raw).items():
            assert "growth" in m, f"{name} missing 'growth'"
            assert "inflation" in m, f"{name} missing 'inflation'"
            assert "regime" in m, f"{name} missing 'regime'"
            assert "confidence" in m, f"{name} missing 'confidence'"
            assert "type" in m, f"{name} missing 'type'"
            assert m["regime"] in ("Goldilocks", "Reflation", "Stagflation", "Deflation")
            assert 0.0 <= m["confidence"] <= 1.0

    def test_empty_raw_does_not_crash(self):
        for fn in (blackrock_method, level_method, aqr_momentum_method):
            result = fn({})
            assert "regime" in result

    # ── Engine integration ────────────────────────────────────────────────────

    def test_engine_populates_regime_methods(self):
        raw = {
            "CFNAI": self._cfnai_above_zero(),
            "CPILFESL": _falling(60, start=110.0, slope=0.1),
            "UNRATE": _flat(60, val=4.0),
        }
        state = compute_macro_state(date(2024, 1, 1), raw)
        assert "vote" in state.regime_methods
        assert "blackrock" in state.regime_methods
        assert "level" in state.regime_methods
        assert "aqr_momentum" in state.regime_methods

    def test_engine_vote_entry_matches_primary_regime(self):
        raw = {"CFNAI": self._cfnai_above_zero()}
        state = compute_macro_state(date(2024, 6, 1), raw)
        assert state.regime_methods["vote"]["regime"] == state.regime
        assert state.regime_methods["vote"]["growth"] == state.growth_direction
        assert state.regime_methods["vote"]["inflation"] == state.inflation_direction
