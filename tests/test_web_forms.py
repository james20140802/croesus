from croesus.profiles.seed_default_profile import DEFAULT_PROFILE
from croesus.web.forms import parse_profile_form


def _base_form():
    return {
        "expected_annual_return": "0.10", "max_tolerable_drawdown": "-0.25",
        "investment_horizon_years": "10", "monthly_contribution": "1000",
        "liquidity_buffer_months": "6", "max_single_position_weight": "0.10",
        "max_sector_weight": "0.35", "max_industry_weight": "0.25",
        "max_theme_weight": "0.30", "max_country_weight": "0.90",
        "max_currency_weight": "0.95", "max_monthly_turnover": "0.15",
        "rebalance_band": "0.05", "trade_mode": "propose_only",
        # 슬리브: 합 1.0
        "sleeve_name": ["core_us_equity", "cash"],
        "target_weight": ["0.9", "0.1"],
        "min_weight": ["", ""], "max_weight": ["", ""],
    }


def test_parse_profile_form_valid():
    profile, targets, errors = parse_profile_form(_base_form(), DEFAULT_PROFILE)
    assert errors == []
    assert abs(sum(t.target_weight for t in targets) - 1.0) < 1e-9
    assert profile.expected_annual_return == 0.10


def test_parse_profile_form_rejects_bad_weights():
    form = _base_form()
    form["target_weight"] = ["0.7", "0.1"]  # 합 0.8 != 1
    _, _, errors = parse_profile_form(form, DEFAULT_PROFILE)
    assert any("1.0" in e or "합" in e for e in errors)


def test_parse_profile_form_rejects_positive_drawdown():
    form = _base_form()
    form["max_tolerable_drawdown"] = "0.25"  # 양수 = 무효
    _, _, errors = parse_profile_form(form, DEFAULT_PROFILE)
    assert any("drawdown" in e.lower() or "드로다운" in e for e in errors)
