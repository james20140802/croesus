"""자동 데이터 갱신 스케줄러(croesus.web.scheduler) 검증."""
from __future__ import annotations

import asyncio
from datetime import datetime, time

import pytest

from croesus.web.scheduler import DataScheduler, parse_run_at


def test_parse_run_at_valid():
    assert parse_run_at("18:00") == time(18, 0)
    assert parse_run_at(" 09:30 ") == time(9, 30)


@pytest.mark.parametrize("bad", ["1800", "25:00", "18:99", "noon"])
def test_parse_run_at_rejects_bad(bad):
    with pytest.raises(ValueError):
        parse_run_at(bad)


def test_next_run_today_if_future():
    now = datetime(2026, 6, 29, 10, 0)
    s = DataScheduler("db", time(18, 0), now=lambda: now)
    assert s.state.next_run == datetime(2026, 6, 29, 18, 0)


def test_next_run_rolls_to_tomorrow_if_past():
    now = datetime(2026, 6, 29, 20, 0)
    s = DataScheduler("db", time(18, 0), now=lambda: now)
    assert s.state.next_run == datetime(2026, 6, 30, 18, 0)


def test_run_once_success_updates_state():
    calls = []

    def fake_refresh(db_path, log):
        calls.append(db_path)
        log("작업 수행")

    now = datetime(2026, 6, 29, 18, 0)
    s = DataScheduler("the-db", time(18, 0), refresh=fake_refresh, now=lambda: now)
    asyncio.run(s.run_once())

    assert calls == ["the-db"]
    assert s.state.last_status == "성공"
    assert s.state.last_error == ""
    assert s.state.running is False
    assert s.state.last_run == now


def test_run_once_failure_is_captured_not_raised():
    def boom(db_path, log):
        raise RuntimeError("yfinance 다운")

    s = DataScheduler("db", time(18, 0), refresh=boom, now=lambda: datetime(2026, 6, 29, 18, 0))
    asyncio.run(s.run_once())  # 예외가 밖으로 전파되지 않아야 한다

    assert s.state.last_status == "실패"
    assert "yfinance 다운" in s.state.last_error
    assert s.state.running is False


def test_shortlist_is_candidates_union_holdings_without_cash(tmp_path):
    from datetime import date

    from croesus.db.connection import get_connection
    from croesus.db.migrate import migrate
    from croesus.web.scheduler import _shortlist_asset_ids

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 29)
    with get_connection(db_path) as conn:
        # 최신 스크리닝: 후보 1개 + 관찰 1개(제외돼야 함)
        conn.execute(
            "INSERT INTO screening_results (run_id, asset_id, score, rank, "
            "decision_bucket) VALUES (?, ?, ?, ?, ?)",
            ["run-1", "US_EQ_CAND", 0.9, 1, "candidate"],
        )
        conn.execute(
            "INSERT INTO screening_results (run_id, asset_id, score, rank, "
            "decision_bucket) VALUES (?, ?, ?, ?, ?)",
            ["run-1", "US_EQ_WATCH", 0.5, 2, "watch"],
        )
        # 보유: 종목 1개 + 현금(제외돼야 함)
        for aid, mv in [("US_EQ_HOLD", 100.0), ("CASH_USD", 50.0)]:
            conn.execute(
                "INSERT INTO portfolio_holdings (portfolio_id, asset_id, as_of_date, "
                "quantity, market_value, currency, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ["default", aid, asof, 1.0, mv, "USD", "manual_csv"],
            )
        shortlist = _shortlist_asset_ids(conn)

    assert shortlist == ["US_EQ_CAND", "US_EQ_HOLD"]


def test_research_refresh_skips_gracefully_when_llm_down(tmp_path, monkeypatch):
    # Ollama가 꺼져 있어도 리서치 단계는 전체 갱신을 중단시키지 않는다.
    from datetime import date

    from croesus.assets.models import Asset
    from croesus.assets.repository import AssetRepository
    from croesus.db.connection import get_connection
    from croesus.db.migrate import migrate
    from croesus.research.llm_client import LlmUnavailable
    from croesus.research.thesis_models import ThesisRunResult
    from croesus.web import scheduler

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    # _run_research_refresh가 부르는 grade_theses를 LLM 미가용 결과로 대체한다.
    def fake_grade(conn, **kwargs):
        result = ThesisRunResult(run_id=kwargs.get("run_id", "r"))
        result.skipped_reason = "server down"
        return result

    monkeypatch.setattr(
        "croesus.research.thesis_grader.grade_theses", fake_grade
    )
    # 밴드 계산이 LLM 미가용 시 호출되지 않아야 함을 보장하기 위해 폭발하게 둔다.
    def boom(*a, **k):
        raise AssertionError("LLM 미가용이면 밴드 계산까지 가면 안 된다")

    monkeypatch.setattr(
        "croesus.factors.equity.compute_valuation."
        "compute_and_store_valuation_factors", boom
    )

    logs: list[str] = []
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO screening_results (run_id, asset_id, score, rank, "
            "decision_bucket) VALUES (?, ?, ?, ?, ?)",
            ["run-1", "US_EQ_CAND", 0.9, 1, "candidate"],
        )
        AssetRepository(conn).upsert_many([Asset(
            asset_id="US_EQ_CAND", symbol="CAND", name="Cand Inc.", asset_type="equity",
        )])
        scheduler._run_research_refresh(conn, logs.append)  # 예외 없이 반환

    assert any("LLM 미가용" in m for m in logs)


def test_state_as_dict_is_template_friendly():
    s = DataScheduler("db", time(8, 30), now=lambda: datetime(2026, 6, 29, 7, 0))
    d = s.state.as_dict()
    assert d["enabled"] is True
    assert d["run_at"] == "08:30"
    assert d["next_run"] == "2026-06-29 08:30"
    assert d["last_run"] is None
