"""웹 서버가 떠 있는 동안 정해진 시각에 데이터 수집·처리를 자동 실행한다.

새 의존성 없이 asyncio 백그라운드 태스크로 동작한다. 매일 지정한 로컬 시각이
되면 데이터 파이프라인(시세·팩터 → 스크리닝)을 한 번 돌린다. 파이프라인은
DuckDB에 쓰기 위해 파일을 잠그므로, 그동안 웹 페이지는 기존의 "데이터 갱신 중"
화면(DataUpdatingError)으로 자연스럽게 대체된다.

DuckDB는 동기 라이브러리이므로 실제 작업은 스레드풀에서 실행해 이벤트 루프를
막지 않는다.
"""
from __future__ import annotations

import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable


def _default_refresh(db_path: str | Path, log: Callable[[str], None]) -> None:
    """기본 자동 갱신: 일일 시세/팩터 파이프라인 + 스크리닝.

    macro는 별도 cadence(주간/월간)로 갱신되므로 일일 자동 갱신에는 포함하지 않는다.
    하나가 실패해도 다음 단계를 시도한다.
    """
    from croesus.web.db import get_write_connection
    from croesus.jobs.daily_run import run_daily_pipeline
    from croesus.jobs.screening_run import run_screening_job

    with get_write_connection(db_path) as conn:
        log("일일 파이프라인 시작 (시세·환율·팩터)")
        run_daily_pipeline(conn, log=log)
        log("스크리닝 실행")
        try:
            run_screening_job(conn)
        except Exception as exc:  # 스크리닝 실패가 전체를 막지 않도록
            log(f"스크리닝 건너뜀: {exc}")


@dataclass
class SchedulerState:
    enabled: bool = False
    run_at: str = ""               # "HH:MM"
    running: bool = False          # 지금 갱신 중인지
    last_run: datetime | None = None
    last_status: str = ""          # "성공" | "실패" | ""
    last_error: str = ""
    next_run: datetime | None = None
    log_tail: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        def fmt(dt: datetime | None) -> str | None:
            return dt.strftime("%Y-%m-%d %H:%M") if dt else None
        return {
            "enabled": self.enabled,
            "run_at": self.run_at,
            "running": self.running,
            "last_run": fmt(self.last_run),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "next_run": fmt(self.next_run),
            "log_tail": list(self.log_tail),
        }


class DataScheduler:
    def __init__(
        self,
        db_path: str | Path,
        run_at: time,
        *,
        refresh: Callable[[str | Path, Callable[[str], None]], None] = _default_refresh,
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._db_path = db_path
        self._run_at = run_at
        self._refresh = refresh
        self._now = now
        self._task: asyncio.Task | None = None
        self.state = SchedulerState(enabled=True, run_at=run_at.strftime("%H:%M"))
        self.state.next_run = self._compute_next(self._now())

    # ── 다음 실행 시각 계산 ───────────────────────────────────────────────
    def _compute_next(self, ref: datetime) -> datetime:
        candidate = ref.replace(
            hour=self._run_at.hour, minute=self._run_at.minute, second=0, microsecond=0
        )
        if candidate <= ref:
            candidate += timedelta(days=1)
        return candidate

    def _log(self, msg: str) -> None:
        stamp = self._now().strftime("%H:%M:%S")
        line = f"[{stamp}] {msg}"
        print(f"[scheduler] {line}", flush=True)
        tail = self.state.log_tail
        tail.append(line)
        del tail[:-20]  # 최근 20줄만 보관

    # ── 백그라운드 루프 ───────────────────────────────────────────────────
    async def _loop(self) -> None:
        self._log(f"자동 갱신 활성화 — 매일 {self.state.run_at}")
        while True:
            now = self._now()
            self.state.next_run = self._compute_next(now)
            wait = max(1.0, (self.state.next_run - now).total_seconds())
            # 길게 자되 중간에 깨어나 취소·시계 변화에 반응
            try:
                while wait > 0:
                    chunk = min(wait, 60.0)
                    await asyncio.sleep(chunk)
                    wait = (self.state.next_run - self._now()).total_seconds()
            except asyncio.CancelledError:
                self._log("자동 갱신 중지")
                raise
            await self.run_once()

    async def run_once(self) -> None:
        """한 번 갱신을 실행(스케줄 도달 또는 수동 트리거)."""
        if self.state.running:
            self._log("이미 갱신 중 — 건너뜀")
            return
        self.state.running = True
        self._log("데이터 갱신 시작")
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._refresh, self._db_path, self._log
            )
            self.state.last_status = "성공"
            self.state.last_error = ""
            self._log("데이터 갱신 완료")
        except Exception as exc:  # noqa: BLE001 — 루프가 죽지 않도록 모두 흡수
            self.state.last_status = "실패"
            self.state.last_error = str(exc)
            self._log(f"데이터 갱신 실패: {exc}")
            traceback.print_exc()
        finally:
            self.state.running = False
            self.state.last_run = self._now()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None


def parse_run_at(value: str) -> time:
    """'HH:MM' 문자열을 time으로 파싱."""
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError(f"시각 형식은 HH:MM 이어야 합니다: {value!r}")
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError(f"시각 범위를 벗어났습니다: {value!r}")
    return time(hour=hh, minute=mm)
