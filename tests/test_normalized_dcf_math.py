from __future__ import annotations

import math

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
