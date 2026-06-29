import time
from croesus.web.cache import TTLCache


def test_ttl_cache_caches_then_expires():
    cache = TTLCache(ttl_seconds=0.05)
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return calls["n"]

    assert cache.get_or_set("k", factory) == 1
    assert cache.get_or_set("k", factory) == 1  # 캐시 hit
    time.sleep(0.06)
    assert cache.get_or_set("k", factory) == 2  # 만료 후 재계산


def test_ttl_cache_invalidate():
    cache = TTLCache(ttl_seconds=100)
    cache.get_or_set("k", lambda: 1)
    cache.invalidate()
    assert cache.get_or_set("k", lambda: 2) == 2


from datetime import date
from fastapi.testclient import TestClient
from croesus.web import create_app
from croesus.web import services
from croesus.web.viewmodels import MacroView


def _client_with(monkeypatch, **patches):
    for name, value in patches.items():
        monkeypatch.setattr(services, name, lambda *a, _v=value, **k: _v)
    return TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)


def test_macro_page_renders(monkeypatch):
    view = MacroView(
        date=date(2026, 6, 22), regime="Goldilocks", positioning="Aggressive",
        regime_confidence=0.8, amplifier_score=30.0, confirmation_score=0.4,
        warnings=[], opportunities=[], regime_methods={}, history=[],
    )
    # read 연결을 막기 위해 라우트가 호출하는 build_macro_view를 패치
    monkeypatch.setattr("croesus.web.routes.macro.build_macro_view", lambda conn: view)
    monkeypatch.setattr("croesus.web.routes.macro.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/macro")
    assert resp.status_code == 200
    # 원시 영문 상수가 아니라 한국어 라벨이 노출되어야 한다
    assert "골디락스" in resp.text
    assert "공격적" in resp.text
    assert "Goldilocks" not in resp.text


def test_macro_page_renders_with_null_amplifier(monkeypatch):
    # amplifier_score가 NULL이어도 500이 아니라 정상 렌더되어야 한다
    view = MacroView(
        date=date(2026, 6, 22), regime="Reflation", positioning="Neutral",
        regime_confidence=0.7, amplifier_score=None, confirmation_score=0.0,
        warnings=[], opportunities=[], regime_methods={}, history=[],
    )
    monkeypatch.setattr("croesus.web.routes.macro.build_macro_view", lambda conn: view)
    monkeypatch.setattr("croesus.web.routes.macro.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/macro")
    assert resp.status_code == 200
    assert "리플레이션" in resp.text


def test_screening_page_renders(monkeypatch):
    from croesus.web.viewmodels import ScreeningView, ScreeningRow
    view = ScreeningView(run_id="screening-2026-06-21-abcd1234", as_of_date=date(2026,6,21),
        rows=[ScreeningRow(rank=1, symbol="NVDA", name="Nvidia", score=0.91,
              decision_bucket="shortlist", reason="strong momentum",
              factor_scores={"momentum_score": 0.9})])
    monkeypatch.setattr("croesus.web.routes.screening.build_screening_view",
                        lambda conn, bucket=None: view)
    monkeypatch.setattr("croesus.web.routes.screening.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/screening")
    assert resp.status_code == 200
    assert "NVDA" in resp.text


def test_portfolio_page_renders(monkeypatch):
    from croesus.web.viewmodels import PortfolioView
    view = PortfolioView(as_of_date=date(2026,6,21), total_market_value=100000.0,
        unrealized_pnl=5000.0,
        holdings=[{"symbol":"AAPL","quantity":10,"market_value":2000.0,"weight":0.02}],
        exposures=[{"exposure_type":"sector","exposure_name":"Tech","weight":0.4,
                    "limit_weight":0.35,"is_violation":True}],
        drifts=[{"sleeve_name":"core_us_equity","current_weight":0.6,"target_weight":0.55,
                 "drift":0.05,"is_outside_band":False}],
        actions=[{"action_type":"trim","human_readable_reason":"섹터 과다",
                  "reason_codes":["SECTOR_OVER_MAX"],"estimated_trade_value":1500.0}])
    monkeypatch.setattr("croesus.web.routes.portfolio.build_portfolio_view", lambda conn: view)
    monkeypatch.setattr("croesus.web.routes.portfolio.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/portfolio")
    assert resp.status_code == 200
    assert "AAPL" in resp.text and "섹터 과다" in resp.text


def test_opportunities_page_renders_with_gate(monkeypatch):
    from croesus.web.viewmodels import OpportunityView, OpportunityRow
    view = OpportunityView(as_of_date=date(2026,6,20), gate_summary={"pass":1,"warn":0,"block":1},
        rows=[
          OpportunityRow(asset_id="a1", symbol="MSFT", name="Microsoft", current_price=400.0,
            base_upside_pct=0.25, bands={"bear":350,"base":500,"bull":650},
            grades={"moat":"A","tech":"B"}, confidence="high",
            gate_status="pass", gate_reason_codes=[], gate_notes=[]),
          OpportunityRow(asset_id="a2", symbol="TSLA", name="Tesla", current_price=200.0,
            base_upside_pct=0.10, bands={"bear":150,"base":260,"bull":350},
            grades={"moat":"B"}, confidence="medium",
            gate_status="block", gate_reason_codes=["SECTOR_OVER_MAX"],
            gate_notes=["섹터 한도 초과"]),
        ])
    monkeypatch.setattr("croesus.web.routes.opportunity.build_opportunity_view",
                        lambda conn, gate=None: view)
    monkeypatch.setattr("croesus.web.routes.opportunity.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/opportunities")
    assert resp.status_code == 200
    assert "MSFT" in resp.text and "TSLA" in resp.text
    # 원시 코드 대신 한국어 라벨이 노출되어야 한다
    assert "섹터 비중이 상한을 넘었습니다" in resp.text   # SECTOR_OVER_MAX 한국어 라벨
    assert "편입 불가" in resp.text                       # block 게이트 상태 한국어
    assert "pill--bad" in resp.text                       # block → bad 톤(심각도 색) 클래스
    assert "SECTOR_OVER_MAX" not in resp.text


def test_opportunity_detail_returns_404_for_unknown_asset(monkeypatch):
    monkeypatch.setattr("croesus.web.routes.opportunity.build_opportunity_detail",
                        lambda conn, asset_id: None)
    monkeypatch.setattr("croesus.web.routes.opportunity.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/opportunities/nonexistent-asset-id")
    assert resp.status_code == 404


def test_home_aggregates(monkeypatch):
    from croesus.web.viewmodels import HomeView, Badge, MacroView
    macro_detail = MacroView(
        date=date(2026, 6, 22), regime="Goldilocks", positioning="Aggressive",
        regime_confidence=0.8, amplifier_score=30.0, confirmation_score=0.4,
        growth_direction="Expanding", inflation_direction="Falling")
    hv = HomeView(macro=Badge("레짐","Goldilocks","ok"),
        actions=[{"action_type":"trim","reason_ko":"QQQM · 단일 종목 비중이 상한을 넘었습니다",
                  "human_readable_reason":"섹터 과다"}],
        action_count=1, opportunity_count=3, drift_alerts=["현금 비중이 목표 범위를 벗어났습니다"],
        screening_count=12, freshness=[Badge("매크로","2026-06-22","ok")],
        macro_detail=macro_detail)
    monkeypatch.setattr("croesus.web.routes.home.build_home_view", lambda conn: hv)
    monkeypatch.setattr("croesus.web.routes.home.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/")
    assert resp.status_code == 200
    # 한국어 사유 + 한국어 레짐 라벨이 노출되고, 원시 영문 상수는 보이지 않아야 한다
    assert "QQQM · 단일 종목 비중이 상한을 넘었습니다" in resp.text
    assert "골디락스" in resp.text
    assert "Goldilocks" not in resp.text
