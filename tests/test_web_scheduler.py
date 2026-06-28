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


def test_state_as_dict_is_template_friendly():
    s = DataScheduler("db", time(8, 30), now=lambda: datetime(2026, 6, 29, 7, 0))
    d = s.state.as_dict()
    assert d["enabled"] is True
    assert d["run_at"] == "08:30"
    assert d["next_run"] == "2026-06-29 08:30"
    assert d["last_run"] is None
