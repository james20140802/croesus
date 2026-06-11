import math

from croesus.factors.equity.valuation import (
    DEFAULT_TERMINAL_GROWTH,
    EQUITY_RISK_PREMIUM,
    compute_beta,
    compute_fcf_growth,
    compute_multiples,
    compute_wacc,
    sector_percentile,
    two_stage_dcf,
)


def test_compute_multiples_basic_and_null_denominators() -> None:
    m = compute_multiples(
        price=100.0,
        eps=5.0,
        book_value_per_share=25.0,
        market_cap=1000.0,
        total_debt=200.0,
        cash=100.0,
        ebitda=110.0,
        free_cash_flow=50.0,
    )
    assert m.pe_ratio == 20.0
    assert m.pb_ratio == 4.0
    # EV = 1000 + 200 - 100 = 1100; / 110 = 10
    assert m.ev_to_ebitda == 10.0
    assert m.fcf_yield == 0.05

    # zero / negative / None denominators -> None (would corrupt percentile order)
    bad = compute_multiples(
        price=100.0,
        eps=0.0,
        book_value_per_share=-3.0,
        market_cap=1000.0,
        total_debt=0.0,
        cash=0.0,
        ebitda=None,
        free_cash_flow=-10.0,
    )
    assert bad.pe_ratio is None
    assert bad.pb_ratio is None
    assert bad.ev_to_ebitda is None
    assert bad.fcf_yield == -0.01  # negative FCF kept (market_cap denominator > 0)


def test_sector_percentile_orders_cheapest_to_zero() -> None:
    peers = [10.0, 20.0, 30.0, 40.0]
    assert sector_percentile(10.0, peers) == 12.5  # cheapest -> low
    assert sector_percentile(40.0, peers) == 87.5  # priciest -> high
    assert sector_percentile(25.0, peers) == 50.0
    assert sector_percentile(99.0, []) is None
    # all values land in [0, 100]
    for v in peers:
        assert 0.0 <= sector_percentile(v, peers) <= 100.0


def test_compute_wacc() -> None:
    assert compute_wacc(0.045, 1.0) == 0.045 + EQUITY_RISK_PREMIUM
    assert math.isclose(compute_wacc(0.04, 1.2), 0.04 + 1.2 * EQUITY_RISK_PREMIUM)


def test_compute_beta_slope_and_insufficient_data() -> None:
    # market series; asset = 2x market exactly -> beta 2.0
    market = [((-1) ** i) * 0.01 * (1 + i % 3) for i in range(60)]
    asset = [2.0 * r for r in market]
    beta = compute_beta(asset, market)
    assert beta is not None and math.isclose(beta, 2.0, rel_tol=1e-9)

    assert compute_beta([0.01, 0.02], [0.01, 0.02]) is None  # too short
    assert compute_beta([0.01] * 40, [0.0] * 40) is None  # zero market variance


def test_compute_fcf_growth_clipping_and_negatives() -> None:
    # 100 -> 150 over 4 years ~ 10.7% CAGR, within band
    g = compute_fcf_growth([100.0, 110.0, 130.0, 150.0])
    assert g is not None and math.isclose(g, (150 / 100) ** (1 / 3) - 1)

    # explosive growth clipped to +30%
    assert compute_fcf_growth([10.0, 1000.0]) == 0.30
    # collapse clipped to -5%
    assert compute_fcf_growth([1000.0, 1.0]) == -0.05
    # sign change / non-positive endpoint -> None
    assert compute_fcf_growth([-10.0, 50.0]) is None
    assert compute_fcf_growth([50.0]) is None


def test_two_stage_dcf_value_and_guards() -> None:
    result = two_stage_dcf(
        base_fcf=100.0,
        growth_rate=0.10,
        wacc=0.09,
        shares_outstanding=10.0,
        total_debt=50.0,
        cash=20.0,
    )
    assert result is not None
    assert result.terminal_growth_rate == DEFAULT_TERMINAL_GROWTH
    # equity value = EV - debt + cash; per share positive and sane
    assert result.equity_value == result.enterprise_value - 50.0 + 20.0
    assert result.intrinsic_value_per_share > 0

    # divergence: WACC <= terminal growth -> skip
    assert two_stage_dcf(
        base_fcf=100.0, growth_rate=0.05, wacc=0.02,
        shares_outstanding=10.0, total_debt=0.0, cash=0.0,
    ) is None
    # non-positive base FCF -> skip
    assert two_stage_dcf(
        base_fcf=-5.0, growth_rate=0.05, wacc=0.09,
        shares_outstanding=10.0, total_debt=0.0, cash=0.0,
    ) is None
    # no shares -> skip
    assert two_stage_dcf(
        base_fcf=100.0, growth_rate=0.05, wacc=0.09,
        shares_outstanding=0.0, total_debt=0.0, cash=0.0,
    ) is None
