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


def _shortlist_asset_ids(conn) -> list[str]:
    """정성평가/기회 분석 대상 = 최신 스크리닝 후보 ∪ 현재 보유 종목.

    CLAUDE.md 원칙대로 "스크리닝으로 먼저 좁힌 shortlist에만 LLM 심층연구"를
    적용한다. 전 종목(이벤트 코호트 ≈ 전 자산)을 매번 평가하면 자동 갱신이
    수십 분간 대시보드를 막으므로, 의사결정에 실제로 쓰이는 종목으로 한정한다.
    현금/파생 의사(疑似) 자산은 제외한다.
    """
    ids: set[str] = set()
    row = conn.execute("SELECT max(run_id) FROM screening_results").fetchone()
    run_id = row[0] if row else None
    if run_id:
        ids.update(
            r[0]
            for r in conn.execute(
                "SELECT asset_id FROM screening_results "
                "WHERE run_id = ? AND decision_bucket = 'candidate'",
                [run_id],
            ).fetchall()
        )
    hrow = conn.execute("SELECT max(as_of_date) FROM portfolio_holdings").fetchone()
    as_of = hrow[0] if hrow else None
    if as_of is not None:
        ids.update(
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT asset_id FROM portfolio_holdings WHERE as_of_date = ?",
                [as_of],
            ).fetchall()
        )
    return sorted(a for a in ids if not a.startswith("CASH_"))


def _run_research_refresh(conn, log: Callable[[str], None]) -> None:
    """이벤트 스캔 → 정성평가(shortlist) → 내재가치 밴드 계산.

    LLM(Ollama)이 꺼져 있으면 우아하게 건너뛰고(전체 갱신을 막지 않음), 밴드는
    thesis 등급이 있는 종목에만 저장되므로 자연히 shortlist로 한정된다.
    """
    from uuid import uuid4

    from croesus.events.scan import run_event_scan
    from croesus.research.thesis_grader import grade_theses
    from croesus.factors.equity.compute_valuation import (
        compute_and_store_valuation_factors,
    )
    from croesus.factors.equity.compute_normalized_dcf import (
        compute_and_store_normalized_dcf,
    )

    log("이벤트 스캔 (정성평가 대상 선별)")
    try:
        run_event_scan(conn)
    except Exception as exc:  # 이벤트 스캔 실패가 나머지를 막지 않도록
        log(f"이벤트 스캔 건너뜀: {exc}")

    shortlist = _shortlist_asset_ids(conn)
    if not shortlist:
        log("정성평가 대상 없음 — 건너뜀")
        return

    log(f"정성 평가 시작 — 스크리닝 후보+보유 {len(shortlist)}종목 (LLM)")
    try:
        result = grade_theses(conn, run_id=uuid4().hex, only_asset_ids=shortlist, log=log)
    except Exception as exc:  # LLM 외 예기치 못한 오류도 갱신을 막지 않도록
        log(f"정성 평가 건너뜀: {exc}")
        return
    if result.skipped_reason:
        log(f"정성 평가 건너뜀 — LLM 미가용: {result.skipped_reason}")
        return
    log(f"정성 평가 완료 — 생성 {result.generated} 실패 {result.failed}")

    log("내재가치 밴드 계산 (기회 카드)")
    try:
        compute_and_store_valuation_factors(conn, include_dcf=True, log=lambda _m: None)
        compute_and_store_normalized_dcf(conn, log=lambda _m: None)
        log("내재가치 밴드 계산 완료")
    except Exception as exc:
        log(f"밴드 계산 건너뜀: {exc}")


def run_default_refresh(
    db_path: str | Path,
    log: Callable[[str], None],
    *,
    include_research: bool = True,
) -> None:
    """기본 자동 갱신: 일일 시세/팩터 파이프라인 + 스크리닝 + (선택) 리서치.

    macro는 별도 cadence(주간/월간)로 갱신되므로 일일 자동 갱신에는 포함하지 않는다.
    리서치 단계(정성평가·기회 밴드)는 스크리닝이 만든 shortlist를 입력으로 쓰므로
    스크리닝 뒤에 실행한다. 한 단계가 실패해도 다음 단계를 시도하고, 끝나면 기회
    캐시를 비워 웹이 즉시 최신 데이터를 보여주게 한다.
    """
    from croesus.web.db import get_write_connection
    from croesus.web import services
    from croesus.jobs.daily_run import run_daily_pipeline
    from croesus.jobs.screening_run import run_screening_job

    with get_write_connection(db_path) as conn:
        log("일일 파이프라인 시작 (시세·환율·팩터)")
        try:
            run_daily_pipeline(conn, log=log)
        except Exception as exc:  # 파이프라인 실패가 스크리닝을 막지 않도록
            log(f"일일 파이프라인 실패: {exc}")
        log("스크리닝 실행")
        try:
            run_screening_job(conn)
        except Exception as exc:  # 스크리닝 실패가 전체를 막지 않도록
            log(f"스크리닝 건너뜀: {exc}")
        if include_research:
            try:
                _run_research_refresh(conn, log)
            except Exception as exc:  # 리서치 실패가 전체를 막지 않도록
                log(f"리서치 갱신 건너뜀: {exc}")

    # DB가 갱신됐으므로 TTL 만료를 기다리지 않고 기회 캐시를 즉시 무효화한다.
    services.opportunity_cache.invalidate()
    log("기회 캐시 무효화 완료")


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
        refresh: Callable[[str | Path, Callable[[str], None]], None] = run_default_refresh,
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
        # _log는 스레드풀에서도 호출되므로, as_dict()가 읽는 리스트를 제자리 변형하지
        # 않고 한 번에 새 리스트로 교체한다(읽는 쪽은 항상 일관된 스냅샷을 본다).
        self.state.log_tail = (self.state.log_tail + [line])[-20:]  # 최근 20줄만 보관

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
        # asyncio 태스크는 취소하지만, 이미 스레드풀에서 돌고 있는 갱신 작업은
        # 중간에 죽이지 않는다 — DuckDB 쓰기 트랜잭션을 강제 중단하는 것보다
        # 끝까지 기다리는 편이 안전하다. 그래서 종료가 잠깐 늦어질 수 있다.
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
