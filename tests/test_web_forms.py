import csv
import io

from croesus.profiles.seed_default_profile import DEFAULT_PROFILE
from croesus.web.forms import holdings_form_to_csv, parse_profile_form, parse_transaction_form


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


def test_holdings_form_to_csv():
    form = {"symbol": ["AAPL", "CASH"], "quantity": ["10", ""],
            "avg_cost": ["150", ""], "currency": ["USD", "USD"],
            "market_value": ["", "500"]}
    text = holdings_form_to_csv(form)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["symbol"] == "AAPL" and rows[0]["quantity"] == "10"
    assert rows[1]["symbol"] == "CASH" and rows[1]["market_value"] == "500"


def test_holdings_form_to_csv_skips_empty_rows():
    form = {"symbol": ["AAPL", ""], "quantity": ["10", ""], "avg_cost": ["150", ""],
            "currency": ["USD", ""], "market_value": ["", ""]}
    text = holdings_form_to_csv(form)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1


def test_parse_transaction_buy_valid():
    txn, errors = parse_transaction_form({
        "transaction_type":"buy","asset_id":"a1","quantity":"5","price":"100",
        "currency":"USD","fees":"1","transaction_date":"2026-06-20"}, "default")
    assert errors == [] and txn is not None
    assert txn.transaction_type == "buy" and txn.quantity == 5.0


def test_parse_transaction_rejects_bad_quantity():
    txn, errors = parse_transaction_form({
        "transaction_type":"buy","asset_id":"a1","quantity":"-5","price":"100",
        "currency":"USD","fees":"0","transaction_date":"2026-06-20"}, "default")
    assert errors
