from __future__ import annotations

import pytest

from croesus.factors.equity.normalized import (
    loglinear_fcf_growth,
    normalized_base_fcf,
)


def test_normalized_base_fcf_is_median_not_last():
    # last year is a trough; median ignores it.
    assert normalized_base_fcf([111.4, 99.6, 108.8, 98.8]) == pytest.approx(104.2)


def test_normalized_base_fcf_empty_is_none():
    assert normalized_base_fcf([]) is None


def test_loglinear_growth_recovers_constant_rate():
    series = [100.0 * 1.10**i for i in range(6)]  # exact 10%/yr
    assert loglinear_fcf_growth(series) == pytest.approx(0.10, abs=1e-9)


def test_loglinear_growth_ignores_endpoint_spike():
    # flat ~100 with one peak first year -> log-linear stays near 0, unlike endpoint CAGR.
    series = [130.0, 100.0, 101.0, 99.0, 100.0]
    g = loglinear_fcf_growth(series)
    assert -0.10 < g < 0.02


def test_loglinear_growth_none_when_too_few_positive_points():
    assert loglinear_fcf_growth([-5.0, -3.0, 10.0]) is None  # only 1 positive point


def test_loglinear_growth_is_clipped():
    series = [1.0 * 3.0**i for i in range(5)]  # 200%/yr, must clip to cap
    from croesus.factors.equity.valuation import FCF_GROWTH_CAP
    assert loglinear_fcf_growth(series) == pytest.approx(FCF_GROWTH_CAP)


def test_reverse_dcf_recovers_known_growth():
    from croesus.factors.equity.normalized import reverse_dcf_implied_growth
    from croesus.factors.equity.valuation import DEFAULT_DCF_KNOBS, two_stage_dcf
    kw = dict(base_fcf=100.0, wacc=0.10, shares_outstanding=10.0,
              total_debt=0.0, cash=0.0, knobs=DEFAULT_DCF_KNOBS)
    forward = two_stage_dcf(growth_rate=0.10, **kw)
    price = forward.intrinsic_value_per_share
    implied = reverse_dcf_implied_growth(price=price, **kw)
    assert implied == pytest.approx(0.10, abs=1e-4)


def test_reverse_dcf_none_when_price_above_search_range():
    from croesus.factors.equity.normalized import reverse_dcf_implied_growth
    # Absurdly high price -> implied growth > hi cap -> None (out of range).
    implied = reverse_dcf_implied_growth(
        price=1e12, base_fcf=100.0, wacc=0.10, shares_outstanding=10.0,
        total_debt=0.0, cash=0.0)
    assert implied is None


def test_reverse_dcf_none_on_invalid_inputs():
    from croesus.factors.equity.normalized import reverse_dcf_implied_growth
    assert reverse_dcf_implied_growth(
        price=50.0, base_fcf=-1.0, wacc=0.10, shares_outstanding=10.0,
        total_debt=0.0, cash=0.0) is None


def _ok_kwargs(annual_fcf, price):
    return dict(annual_fcf=annual_fcf, price=price, wacc=0.10,
                shares_outstanding=10.0, total_debt=0.0, cash=0.0)


def test_evaluate_marks_financials_not_meaningful():
    from croesus.factors.equity.normalized import (
        QUALITY_FCF_NOT_MEANINGFUL, evaluate_normalized_dcf)
    res = evaluate_normalized_dcf(**_ok_kwargs([107.0, 13.0, -42.0, -148.0], 50.0))
    assert res.valuation_quality == QUALITY_FCF_NOT_MEANINGFUL
    assert res.normalized_intrinsic_value_per_share is None
    assert res.implied_growth is None


def test_evaluate_flags_short_history():
    from croesus.factors.equity.normalized import (
        QUALITY_SHORT_HISTORY, evaluate_normalized_dcf)
    res = evaluate_normalized_dcf(**_ok_kwargs([100.0, 104.0, 102.0], 50.0))
    assert res.valuation_quality == QUALITY_SHORT_HISTORY
    assert res.n_fcf_years == 3


def test_evaluate_full_result_ok():
    from croesus.factors.equity.normalized import QUALITY_OK, evaluate_normalized_dcf
    series = [100.0, 102.0, 101.0, 103.0, 102.0]  # ~flat, all positive, 5 yrs
    res = evaluate_normalized_dcf(**_ok_kwargs(series, 200.0))
    assert res.valuation_quality == QUALITY_OK
    assert res.normalized_base_fcf == pytest.approx(102.0)  # median
    assert res.normalized_intrinsic_value_per_share is not None
    assert res.implied_growth is not None
    # priced well above the ~flat normalized intrinsic -> positive plausibility gap
    assert res.plausibility_gap > 0
