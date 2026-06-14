"""Quality + low-beta multi-factor completion: metrics and score wiring."""
from __future__ import annotations

from croesus.factors.equity.quality import compute_quality_metrics
from croesus.screening.run_screening import _quality_score, _valuation_score


# ── quality primitives ────────────────────────────────────────────────────────

def test_quality_metrics_compute_roe_margin_leverage() -> None:
    m = compute_quality_metrics(
        net_income=200.0, revenue=1000.0, total_equity=800.0, total_debt=400.0
    )
    assert m["roe"] == 0.25          # 200 / 800
    assert m["net_margin"] == 0.20   # 200 / 1000
    assert m["debt_to_equity"] == 0.5  # 400 / 800


def test_quality_metrics_skip_meaningless_negative_equity() -> None:
    # Negative equity makes ROE/leverage nonsensical — they must be omitted,
    # not produce a misleading negative ratio. Net margin still computes.
    m = compute_quality_metrics(
        net_income=50.0, revenue=500.0, total_equity=-100.0, total_debt=300.0
    )
    assert "roe" not in m
    assert "debt_to_equity" not in m
    assert m["net_margin"] == 0.10


def test_quality_metrics_partial_inputs_omitted_not_fabricated() -> None:
    m = compute_quality_metrics(
        net_income=None, revenue=1000.0, total_equity=500.0, total_debt=None
    )
    assert m == {}  # no net_income → no ROE/margin; no debt → no leverage


# ── score blends (higher = better; leverage inverts) ──────────────────────────

def test_quality_score_inverts_leverage_only() -> None:
    # High ROE/margin percentile + LOW leverage percentile → high quality.
    good = _quality_score({"roe": 0.9, "net_margin": 0.8, "debt_to_equity": 0.1})
    bad = _quality_score({"roe": 0.1, "net_margin": 0.2, "debt_to_equity": 0.9})
    assert good > bad
    # (0.9 + 0.8 + (1 - 0.1)) / 3 = 0.8667
    assert abs(good - (0.9 + 0.8 + 0.9) / 3) < 1e-9


def test_quality_score_none_when_no_quality_factor_present() -> None:
    assert _quality_score({"momentum_1m": 0.5}) is None


def test_valuation_score_still_independent_of_quality() -> None:
    # Regression guard: adding quality must not perturb valuation scoring.
    assert _valuation_score({"pe_ratio": 0.2, "fcf_yield": 0.7}) == (
        (1.0 - 0.2) + 0.7
    ) / 2
