"""Tests for macro → screening wiring: reader + daily_run consumption."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.daily_run import run_daily_pipeline
from croesus.macro._loader import load_latest_macro_state, store_macro_state
from croesus.macro.models import MacroState


def _sample_state(d: date, regime: str = "Goldilocks") -> MacroState:
    return MacroState(
        date=d,
        regime=regime,
        regime_confidence=0.8,
        growth_direction="Expanding",
        inflation_direction="Falling",
        amplifier_score=25.0,
        confirmation_score=0.4,
        positioning="Aggressive",
        warnings=[{"indicator": "VIX", "current": 12.0, "percentile": 10.0, "code": "LOW_VOLATILITY"}],
        opportunities=[{"indicator": "HY", "current": 3.0, "percentile": 5.0, "code": "TIGHT_CREDIT_SPREADS"}],
        raw_indicators={"^VIX": 12.0, "amp_credit": 20.0},
        regime_methods={"vote": {"regime": "Goldilocks", "growth": "Expanding",
                                 "inflation": "Falling", "confidence": 0.8,
                                 "type": "ensemble_vote", "description": "x"}},
    )


class EmptyPriceSource:
    def fetch_daily_prices(self, symbol: str, period: str = "1y"):
        import pandas as pd

        return pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "adjusted_close", "volume"]
        )


# ── Reader ──────────────────────────────────────────────────────────────────

def test_load_latest_returns_none_on_empty_table(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        assert load_latest_macro_state(conn) is None


def test_store_then_load_roundtrips_all_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "rt.duckdb"
    migrate(db_path)
    original = _sample_state(date(2026, 5, 30))
    store_macro_state(original, db_path)

    with get_connection(db_path) as conn:
        loaded = load_latest_macro_state(conn)

    assert loaded is not None
    assert loaded.date == original.date
    assert loaded.regime == original.regime
    assert loaded.regime_confidence == original.regime_confidence
    assert loaded.growth_direction == original.growth_direction
    assert loaded.inflation_direction == original.inflation_direction
    assert loaded.amplifier_score == original.amplifier_score
    assert loaded.confirmation_score == original.confirmation_score
    assert loaded.positioning == original.positioning
    assert loaded.warnings == original.warnings
    assert loaded.opportunities == original.opportunities
    assert loaded.raw_indicators == original.raw_indicators
    assert loaded.regime_methods == original.regime_methods


def test_load_latest_returns_most_recent_date(tmp_path: Path) -> None:
    db_path = tmp_path / "multi.duckdb"
    migrate(db_path)
    store_macro_state(_sample_state(date(2026, 5, 1), regime="Stagflation"), db_path)
    store_macro_state(_sample_state(date(2026, 5, 30), regime="Goldilocks"), db_path)

    with get_connection(db_path) as conn:
        loaded = load_latest_macro_state(conn)

    assert loaded is not None
    assert loaded.date == date(2026, 5, 30)
    assert loaded.regime == "Goldilocks"


# ── daily_run consumption ─────────────────────────────────────────────────────

def test_daily_pipeline_consumes_macro_state_when_present(tmp_path: Path) -> None:
    db_path = tmp_path / "consume.duckdb"
    migrate(db_path)
    store_macro_state(_sample_state(date(2026, 5, 30), regime="Goldilocks"), db_path)

    with get_connection(db_path) as conn:
        result = run_daily_pipeline(conn, source=EmptyPriceSource(), log=lambda m: None)

    assert result.screening_params is not None
    assert result.screening_params["regime"] == "Goldilocks"
    # Goldilocks override bumps momentum above the 0.35 base weight
    assert result.screening_params["factor_weights"]["momentum"] > 0.35


def test_daily_pipeline_uses_neutral_params_when_macro_absent(tmp_path: Path) -> None:
    db_path = tmp_path / "neutral.duckdb"
    migrate(db_path)  # no macro_scores rows

    with get_connection(db_path) as conn:
        result = run_daily_pipeline(conn, source=EmptyPriceSource(), log=lambda m: None)

    assert result.screening_params is not None
    # Neutral fallback: base weights from config, no stress filters
    assert result.screening_params["factor_weights"]["momentum"] == 0.35
    assert result.screening_params["filters"] == {}
    assert result.screening_params["regime"] is None
