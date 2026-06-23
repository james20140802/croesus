from contextlib import contextmanager
from fastapi.testclient import TestClient
from croesus.web import create_app
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE, DEFAULT_POLICY_TARGETS


class _FakeProfileRepo:
    saved = []
    def __init__(self, conn): pass
    def get_profile(self, pid): return DEFAULT_PROFILE
    def get_policy_targets(self, pid): return DEFAULT_POLICY_TARGETS
    def save_profile(self, profile, targets): _FakeProfileRepo.saved.append((profile, targets))


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
