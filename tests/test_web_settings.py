from contextlib import contextmanager
from datetime import date
from fastapi.testclient import TestClient
from croesus.web import create_app
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS
from croesus.web.viewmodels import PortfolioView


class _FakeProfileRepo:
    saved = []
    deleted = []
    profiles = []  # what list_profiles() returns
    def __init__(self, conn): pass
    def get_profile(self, pid):
        return next((p for p in _FakeProfileRepo.profiles if p.profile_id == pid), DEFAULT_PROFILE)
    def get_policy_targets(self, pid): return DEFAULT_POLICY_TARGETS
    def save_profile(self, profile, targets): _FakeProfileRepo.saved.append((profile, targets))
    def list_profiles(self): return list(_FakeProfileRepo.profiles)
    def delete_profile(self, pid): _FakeProfileRepo.deleted.append(pid)


def _patch(monkeypatch):
    monkeypatch.setattr("croesus.web.routes.settings.ProfileRepository", _FakeProfileRepo)
    monkeypatch.setattr("croesus.web.routes.settings.get_read_connection",
                        contextmanager(lambda p: iter([None])))
    monkeypatch.setattr("croesus.web.routes.settings.get_write_connection",
                        contextmanager(lambda p: iter([None])))


def test_profile_get(monkeypatch):
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=False)
    assert client.get("/settings/profile").status_code == 200


def test_profile_post_invalid_shows_errors(monkeypatch):
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=False)
    resp = client.post("/settings/profile", data={
        "expected_annual_return":"0.1","max_tolerable_drawdown":"0.25",  # 양수=무효
        "investment_horizon_years":"10","monthly_contribution":"0",
        "liquidity_buffer_months":"6","max_single_position_weight":"0.1",
        "max_sector_weight":"0.35","max_industry_weight":"0.25","max_theme_weight":"0.3",
        "max_country_weight":"0.9","max_currency_weight":"0.95","max_monthly_turnover":"0.15",
        "rebalance_band":"0.05","trade_mode":"propose_only",
        "sleeve_name":["cash"],"target_weight":["1.0"],"min_weight":[""],"max_weight":[""]})
    assert resp.status_code == 400
    assert "저장할 수 없습니다" in resp.text


def test_profile_post_valid_redirects(monkeypatch):
    _FakeProfileRepo.saved = []
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=True)
    resp = client.post("/settings/profile", data={
        "expected_annual_return": "0.10", "max_tolerable_drawdown": "-0.25",
        "investment_horizon_years": "10", "monthly_contribution": "1000",
        "liquidity_buffer_months": "6", "max_single_position_weight": "0.10",
        "max_sector_weight": "0.35", "max_industry_weight": "0.25",
        "max_theme_weight": "0.30", "max_country_weight": "0.90",
        "max_currency_weight": "0.95", "max_monthly_turnover": "0.15",
        "rebalance_band": "0.05", "trade_mode": "propose_only",
        "sleeve_name": ["core_us_equity", "cash"],
        "target_weight": ["0.9", "0.1"],
        "min_weight": ["", ""], "max_weight": ["", ""],
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert len(_FakeProfileRepo.saved) == 1


def test_holdings_post_recomputes(monkeypatch):
    calls = {}

    def fake_run(conn, path, *, portfolio_id, as_of_date=None):
        calls["path"] = str(path)
        calls["pid"] = portfolio_id

    monkeypatch.setattr("croesus.web.routes.portfolio.run_portfolio_snapshot", fake_run)
    monkeypatch.setattr("croesus.web.routes.portfolio.resolve_portfolio_id", lambda c: "default")
    monkeypatch.setattr("croesus.web.routes.portfolio.get_write_connection",
                        contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=False)
    resp = client.post("/portfolio/holdings", data={
        "symbol": ["AAPL"], "quantity": ["10"], "avg_cost": ["150"],
        "currency": ["USD"], "market_value": [""]}, follow_redirects=False)
    assert resp.status_code == 303
    assert calls["pid"] == "default" and calls["path"].endswith(".csv")


def test_transaction_post_records(monkeypatch):
    recorded = {}
    class _Repo:
        def __init__(self, conn): pass
        def record_transaction(self, txn): recorded["txn"] = txn
    monkeypatch.setattr("croesus.web.routes.portfolio.TransactionRepository", _Repo)
    monkeypatch.setattr("croesus.web.routes.portfolio.resolve_portfolio_id", lambda c: "default")
    monkeypatch.setattr("croesus.web.routes.portfolio.get_read_connection",
                        contextmanager(lambda p: iter([None])))
    monkeypatch.setattr("croesus.web.routes.portfolio.get_write_connection",
                        contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=False)
    resp = client.post("/portfolio/transactions", data={
        "transaction_type":"buy","asset_id":"a1","quantity":"5","price":"100",
        "gross_amount":"","currency":"USD","fees":"1","transaction_date":"2026-06-20"},
        follow_redirects=False)
    assert resp.status_code == 303 and recorded["txn"].asset_id == "a1"


def _valid_profile_form():
    return {
        "expected_annual_return": "0.10", "max_tolerable_drawdown": "-0.25",
        "investment_horizon_years": "10", "monthly_contribution": "1000",
        "liquidity_buffer_months": "6", "max_single_position_weight": "0.10",
        "max_sector_weight": "0.35", "max_industry_weight": "0.25",
        "max_theme_weight": "0.30", "max_country_weight": "0.90",
        "max_currency_weight": "0.95", "max_monthly_turnover": "0.15",
        "rebalance_band": "0.05", "trade_mode": "propose_only",
        "sleeve_name": ["core_us_equity", "cash"], "target_weight": ["0.9", "0.1"],
        "min_weight": ["", ""], "max_weight": ["", ""],
    }


def test_load_house_preset_fills_form(monkeypatch):
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=True)
    resp = client.get("/settings/profile?load=preset:capital_preservation")
    assert resp.status_code == 200
    assert "프리셋을 불러왔어요" in resp.text  # notice rendered


def test_save_as_creates_named_profile(monkeypatch):
    _FakeProfileRepo.saved = []
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=True)
    data = _valid_profile_form() | {"profile_name": "공격적 버전"}
    resp = client.post("/settings/profile/save-as", data=data, follow_redirects=False)
    assert resp.status_code == 303
    assert len(_FakeProfileRepo.saved) == 1
    profile, targets = _FakeProfileRepo.saved[0]
    assert profile.profile_id.startswith("user-") and profile.profile_id != "default"
    assert profile.name == "공격적 버전"
    assert all(t.profile_id == profile.profile_id for t in targets)


def test_save_as_without_name_is_rejected(monkeypatch):
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=False)
    resp = client.post("/settings/profile/save-as",
                       data=_valid_profile_form(), follow_redirects=False)
    assert resp.status_code == 400
    assert "이름을 입력" in resp.text


def test_delete_profile_route(monkeypatch):
    _FakeProfileRepo.deleted = []
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=True)
    resp = client.post("/settings/profile/delete",
                       data={"profile_id": "user-old"}, follow_redirects=False)
    assert resp.status_code == 303
    assert _FakeProfileRepo.deleted == ["user-old"]


def test_delete_refuses_default(monkeypatch):
    _FakeProfileRepo.deleted = []
    _patch(monkeypatch)
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=True)
    client.post("/settings/profile/delete", data={"profile_id": "default"},
                follow_redirects=False)
    assert _FakeProfileRepo.deleted == []  # guarded before reaching the repo


def test_portfolio_edit_prepopulates_existing_holdings(monkeypatch):
    fake_view = PortfolioView(
        as_of_date=date(2026, 6, 23),
        total_market_value=4000.0,
        unrealized_pnl=0.0,
        holdings=[{
            "symbol": "AAPL", "name": "Apple", "quantity": 10,
            "avg_cost": 150.0, "market_value": 2000.0,
            "currency": "USD", "weight": 0.5,
        }],
    )
    monkeypatch.setattr("croesus.web.routes.portfolio.build_portfolio_view",
                        lambda conn: fake_view)
    monkeypatch.setattr("croesus.web.routes.portfolio.get_read_connection",
                        contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=True)
    resp = client.get("/portfolio/edit")
    assert resp.status_code == 200
    assert "150.0" in resp.text, "avg_cost 150.0 not pre-populated in holdings editor"
    assert "2000.0" in resp.text, "market_value 2000.0 not pre-populated in holdings editor"
