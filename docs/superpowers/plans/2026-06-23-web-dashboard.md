# Croesus 웹 대시보드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 파이프라인 결과를 폰·태블릿·PC에서 적응형으로 시각화하고, 프로필·포트폴리오·거래 설정을 웹에서 편집하는 읽기+설정쓰기 웹앱을 만든다.

**Architecture:** 자기완결적 `croesus/web/` 패키지. 읽기는 요청당 `read_only` DuckDB 연결, 설정 저장은 요청당 `read-write` 연결(둘 다 단명, 파이프라인 writer 보호). 라우트는 얇은 `services.py`(읽기 뷰모델)와 `forms.py`(폼→모델+검증)를 통해 **기존 리포지토리/검증/잡을 재사용**한다. 화면은 Jinja2 + HTMX + 벤더링 ECharts, CSS 브레이크포인트로 적응형.

**Tech Stack:** FastAPI · Uvicorn · Jinja2 · python-multipart · HTMX(벤더링) · ECharts(벤더링) · DuckDB · pytest + FastAPI TestClient

## Global Constraints

- Python `>=3.10` (기존 `pyproject.toml` `requires-python`).
- 기존 잡/계산/리포지토리 코드는 **수정 금지** — `croesus/web/` 안에서만 작업(예외: `pyproject.toml` 의존성 추가).
- 추가 의존성 정확히: `fastapi>=0.110`, `uvicorn[standard]>=0.29`, `jinja2>=3.1`, `python-multipart>=0.0.9`.
- htmx·echarts는 `croesus/web/static/js/`에 **벤더링**(CDN 금지). 버전 고정: htmx `2.0.4`, echarts `5.5.1`.
- 기동 관례: `python -m croesus.web` (기존 `python -m croesus.jobs.<job>`와 동일 형태). `[project.scripts]` 추가 안 함.
- DuckDB 연결은 **요청당 개방·즉시 종료**. 한 요청에서 read_only와 read-write를 동시에 보유하지 않는다.
- 기본 포트폴리오 id = `"default"`. 멀티 포트폴리오 비범위.
- 추천 행동은 **표시만**(승인/실행 버튼 없음).
- 테스트는 평면 `tests/test_web_*.py`. 라우트 테스트는 `services`를 monkeypatch해 canned 뷰모델로 렌더 검증(무거운 DB 시딩 회피); `db.py`·`forms.py`·CSV 직렬화는 실제 temp DuckDB/순수함수로 검증.
- 커밋 메시지는 gitmoji 사용(`✨ feat:`, `🧪 tests:`, `🔧 chore:`, `📝 docs:`).

---

## File Structure

```
croesus/web/
  __init__.py            # create_app() 노출
  __main__.py            # argparse + uvicorn.run + tailscale URL 출력
  app.py                 # create_app(db_path) 팩토리: 라우트·예외핸들러·static·templates 마운트
  db.py                  # get_read_connection / get_write_connection / DataUpdatingError
  cache.py               # TTLCache (opportunity 재계산 캐시)
  deps.py                # db_path(request) 의존성, templates 객체
  viewmodels.py          # @dataclass 뷰모델(MacroView, ScreeningView, PortfolioView, OpportunityView, HomeView ...)
  services.py            # 읽기: 기존 repo → 뷰모델. 날짜/포트폴리오 해석, asset symbol map
  forms.py               # 쓰기: 폼 dict → 도메인 모델 + 기존 검증 호출 → 에러 수집
  routes/
    __init__.py
    home.py  macro.py  screening.py  portfolio.py  opportunity.py  settings.py
  templates/
    base.html
    home.html macro.html screening.html portfolio.html opportunities.html opportunity_detail.html
    portfolio_edit.html transactions.html settings_profile.html error_updating.html
    partials/ (holdings_rows.html, screening_table.html, form_errors.html)
  static/
    css/app.css
    js/htmx.min.js js/echarts.min.js js/charts.js
tests/
  _web_helpers.py        # make_client(monkeypatch, **canned), temp_db(path)
  test_web_app.py test_web_db.py test_web_pages.py test_web_forms.py test_web_settings.py
```

각 도메인 페이지는 (service 함수 + route + template + chart + test)를 한 묶음으로 만들어 독립 검증한다.

---

### Task 1: 패키지 골격 · 앱 팩토리 · 기동 · 헬스체크

**Files:**
- Modify: `pyproject.toml` (dependencies 추가)
- Create: `croesus/web/__init__.py`, `croesus/web/app.py`, `croesus/web/deps.py`, `croesus/web/__main__.py`
- Create: `croesus/web/templates/base.html`, `croesus/web/templates/home.html`
- Create: `croesus/web/static/css/app.css`, `croesus/web/static/js/.gitkeep`
- Create: `croesus/web/routes/__init__.py`, `croesus/web/routes/home.py`
- Test: `tests/test_web_app.py`, `tests/_web_helpers.py`

**Interfaces:**
- Produces: `create_app(db_path: str | Path | None = None) -> FastAPI`. `app.state.db_path` 보관. `deps.get_db_path(request) -> Path`. `deps.templates` (Jinja2Templates). 라우트 `GET /healthz` → `{"status":"ok"}`, `GET /` → home.html(스텁).

- [ ] **Step 1: 의존성 추가**

`pyproject.toml`의 `dependencies` 리스트 끝(`"xlrd>=2.0",` 다음 줄)에 추가:

```toml
  # web dashboard
  "fastapi>=0.110",
  "uvicorn[standard]>=0.29",
  "jinja2>=3.1",
  "python-multipart>=0.0.9",
```

설치: `uv sync` (또는 `pip install -e .`).

- [ ] **Step 2: 실패 테스트 작성** — `tests/_web_helpers.py`

```python
from __future__ import annotations
from pathlib import Path
from croesus.web import create_app


def make_app(db_path: Path | str = "storage/croesus.duckdb"):
    return create_app(db_path)
```

`tests/test_web_app.py`:

```python
from fastapi.testclient import TestClient
from tests._web_helpers import make_app


def test_healthz_ok():
    client = TestClient(make_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_home_renders():
    client = TestClient(make_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Croesus" in resp.text
```

- [ ] **Step 3: 실패 확인**

Run: `pytest tests/test_web_app.py -v`
Expected: FAIL — `ModuleNotFoundError: croesus.web`.

- [ ] **Step 4: deps.py 작성** — `croesus/web/deps.py`

```python
from __future__ import annotations
from pathlib import Path
from fastapi import Request
from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def get_db_path(request: Request) -> Path:
    return Path(request.app.state.db_path)
```

- [ ] **Step 5: app.py 팩토리 작성** — `croesus/web/app.py`

```python
from __future__ import annotations
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from croesus.db.connection import resolve_db_path
from croesus.web.routes import home

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(db_path: str | Path | None = None) -> FastAPI:
    app = FastAPI(title="Croesus Dashboard", docs_url=None, redoc_url=None)
    app.state.db_path = str(resolve_db_path(db_path))
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(home.router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
```

- [ ] **Step 6: `__init__.py`** — `croesus/web/__init__.py`

```python
from croesus.web.app import create_app

__all__ = ["create_app"]
```

- [ ] **Step 7: home 라우트 스텁** — `croesus/web/routes/__init__.py` (빈 파일) + `croesus/web/routes/home.py`

```python
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "home.html", {"title": "오늘 한눈에"}
    )
```

- [ ] **Step 8: base.html + home.html (스텁)**

`croesus/web/templates/base.html`:

```html
<!doctype html>
<html lang="ko" data-theme="auto">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }} · Croesus</title>
  <link rel="stylesheet" href="/static/css/app.css">
  <script src="/static/js/htmx.min.js" defer></script>
  <script src="/static/js/echarts.min.js" defer></script>
  <script src="/static/js/charts.js" defer></script>
</head>
<body>
  <header class="topbar"><a href="/" class="brand">Croesus</a></header>
  <main class="container">{% block content %}{% endblock %}</main>
  <nav class="bottomnav">
    <a href="/">홈</a><a href="/macro">매크로</a><a href="/screening">스크리닝</a>
    <a href="/portfolio">포트폴리오</a><a href="/opportunities">기회</a>
    <a href="/settings/profile">설정</a>
  </nav>
</body>
</html>
```

`croesus/web/templates/home.html`:

```html
{% extends "base.html" %}
{% block content %}<h1>{{ title }}</h1><p>대시보드 준비 중</p>{% endblock %}
```

- [ ] **Step 9: 빈 CSS/JS 자리** — `croesus/web/static/css/app.css`에 최소 스타일, `croesus/web/static/js/.gitkeep` 생성. 벤더링 JS는 Task 12에서 채움. charts.js는 일단 빈 파일 생성:

```bash
printf "/* charts init — filled in Task 12 */\n" > croesus/web/static/js/charts.js
: > croesus/web/static/js/htmx.min.js
: > croesus/web/static/js/echarts.min.js
```

`app.css` 최소:

```css
:root { color-scheme: light dark; }
body { font-family: system-ui, sans-serif; margin: 0; }
.container { padding: 1rem; max-width: 1200px; margin: 0 auto; }
.bottomnav { position: sticky; bottom: 0; display: flex; gap: .5rem; padding: .5rem; }
```

- [ ] **Step 10: `__main__.py`** — `croesus/web/__main__.py`

```python
from __future__ import annotations
import argparse
import socket
import subprocess
from typing import Sequence

import uvicorn


def _tailscale_host() -> str | None:
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=2
        )
        ip = out.stdout.strip().splitlines()
        return ip[0] if ip else None
    except Exception:
        return None


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m croesus.web")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--db-path", default=None)
    return p


def main(argv: Sequence[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    ts = _tailscale_host() or socket.gethostname()
    print(f"Croesus dashboard → http://{ts}:{args.port}  (local: http://127.0.0.1:{args.port})")
    from croesus.web import create_app

    uvicorn.run(create_app(args.db_path), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 11: 테스트 통과 확인**

Run: `pytest tests/test_web_app.py -v`
Expected: PASS (2 passed). 수동: `python -m croesus.web --port 8765` 후 브라우저에서 홈 확인.

- [ ] **Step 12: 커밋**

```bash
git add pyproject.toml croesus/web tests/test_web_app.py tests/_web_helpers.py
git commit -m "✨ feat: web app skeleton, factory, healthz, home stub"
```

---

### Task 2: DuckDB 읽기/쓰기 연결 + 락 폴백

**Files:**
- Create: `croesus/web/db.py`
- Modify: `croesus/web/app.py` (DataUpdatingError 예외 핸들러 + error 템플릿 등록)
- Create: `croesus/web/templates/error_updating.html`
- Test: `tests/test_web_db.py`

**Interfaces:**
- Produces: `DataUpdatingError(Exception)`; `get_read_connection(db_path) -> ContextManager[duckdb.DuckDBPyConnection]`; `get_write_connection(db_path) -> ContextManager[...]`. 락 충돌 시 `DataUpdatingError` raise. 앱 레벨 핸들러가 이를 503 + `error_updating.html`로 변환.

- [ ] **Step 1: 실패 테스트** — `tests/test_web_db.py`

```python
import duckdb
import pytest
from croesus.web.db import get_read_connection, get_write_connection, DataUpdatingError


def _make_db(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t (x INTEGER)")
    con.execute("INSERT INTO t VALUES (42)")
    con.close()


def test_read_connection_reads(tmp_path):
    db = tmp_path / "x.duckdb"
    _make_db(db)
    with get_read_connection(db) as conn:
        assert conn.execute("SELECT x FROM t").fetchone()[0] == 42


def test_read_connection_raises_when_locked(tmp_path):
    db = tmp_path / "x.duckdb"
    _make_db(db)
    holder = duckdb.connect(str(db))  # 외부 read-write 점유
    try:
        with pytest.raises(DataUpdatingError):
            with get_read_connection(db):
                pass
    finally:
        holder.close()


def test_write_connection_writes(tmp_path):
    db = tmp_path / "x.duckdb"
    _make_db(db)
    with get_write_connection(db) as conn:
        conn.execute("INSERT INTO t VALUES (7)")
    with get_read_connection(db) as conn:
        assert conn.execute("SELECT count(*) FROM t").fetchone()[0] == 2
```

> 참고: DuckDB는 한 프로세스가 파일을 read-write로 쥐면 다른 연결이 락 충돌을 일으킨다. 위 `holder`가 그 상황을 만든다.

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_web_db.py -v`
Expected: FAIL — `ModuleNotFoundError: croesus.web.db`.

- [ ] **Step 3: db.py 구현** — `croesus/web/db.py`

```python
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import duckdb

from croesus.db.connection import resolve_db_path


class DataUpdatingError(RuntimeError):
    """DuckDB 파일이 다른 프로세스(데일리 싱크)에 의해 잠겨 있을 때."""


@contextmanager
def _connect(db_path, *, read_only: bool) -> Iterator[duckdb.DuckDBPyConnection]:
    path = resolve_db_path(db_path)
    try:
        conn = duckdb.connect(str(path), read_only=read_only)
    except duckdb.Error as exc:  # IOException 포함 — 락/사용중
        raise DataUpdatingError(str(exc)) from exc
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def get_read_connection(db_path: str | Path | None = None):
    with _connect(db_path, read_only=True) as conn:
        yield conn


@contextmanager
def get_write_connection(db_path: str | Path | None = None):
    with _connect(db_path, read_only=False) as conn:
        yield conn
```

- [ ] **Step 4: 통과 확인**

Run: `pytest tests/test_web_db.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: 앱에 예외 핸들러 + 템플릿**

`croesus/web/templates/error_updating.html`:

```html
{% extends "base.html" %}
{% block content %}
<section class="empty">
  <h1>데이터 동기화 중</h1>
  <p>파이프라인이 데이터를 갱신하는 중입니다. 잠시 후 새로고침해 주세요.</p>
  <button onclick="location.reload()">새로고침</button>
</section>
{% endblock %}
```

`croesus/web/app.py`의 `create_app` 안, 라우터 등록 뒤에 추가:

```python
    from croesus.web.db import DataUpdatingError
    from croesus.web.deps import templates
    from fastapi import Request

    @app.exception_handler(DataUpdatingError)
    async def _updating_handler(request: Request, exc: DataUpdatingError):
        return templates.TemplateResponse(
            request, "error_updating.html", {"title": "동기화 중"}, status_code=503
        )
```

`app.py` 상단 import에 `from fastapi import FastAPI, Request`로 보강.

- [ ] **Step 6: 핸들러 테스트** — `tests/test_web_db.py`에 추가

```python
from fastapi import APIRouter
from fastapi.testclient import TestClient
from croesus.web import create_app


def test_app_returns_503_on_data_updating(tmp_path):
    app = create_app(tmp_path / "missing.duckdb")
    boom = APIRouter()

    @boom.get("/boom")
    def _boom():
        raise DataUpdatingError("locked")

    app.include_router(boom)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/boom")
    assert resp.status_code == 503
    assert "동기화" in resp.text
```

Run: `pytest tests/test_web_db.py -v` → Expected: PASS (4 passed).

- [ ] **Step 7: 커밋**

```bash
git add croesus/web/db.py croesus/web/app.py croesus/web/templates/error_updating.html tests/test_web_db.py
git commit -m "✨ feat: read/write duckdb connections with lock-aware 503 fallback"
```

---

### Task 3: 뷰모델 · 캐시 · 공통 해석(services 기반)

**Files:**
- Create: `croesus/web/viewmodels.py`, `croesus/web/cache.py`, `croesus/web/services.py`
- Test: `tests/test_web_pages.py` (캐시·해석 단위 테스트 시작)

**Interfaces:**
- Produces (viewmodels.py, 전부 `@dataclass(frozen=True)`):
  - `Badge(label: str, value: str, tone: str)` (tone ∈ {"ok","warn","bad","neutral"})
  - `MacroView(date, regime, positioning, regime_confidence, amplifier_score, confirmation_score, warnings: list[dict], opportunities: list[dict], regime_methods: dict, history: list[dict])`
  - `ScreeningRow(rank, symbol, name, score, decision_bucket, reason, factor_scores: dict)`; `ScreeningView(run_id, as_of_date, rows: list[ScreeningRow])`
  - `PortfolioView(as_of_date, total_market_value, unrealized_pnl, holdings: list[dict], exposures: list[dict], drifts: list[dict], actions: list[dict])`
  - `OpportunityRow(asset_id, symbol, name, current_price, base_upside_pct, bands: dict, grades: dict, confidence)`; `OpportunityView(as_of_date, rows: list[OpportunityRow])`
  - `HomeView(macro: Badge|None, actions: list[dict], action_count, opportunity_count, drift_alerts: list[str], screening_count, freshness: list[Badge])`
- Produces (cache.py): `TTLCache(ttl_seconds: float)` with `.get_or_set(key, factory)` and `.invalidate()`.
- Produces (services.py): `resolve_portfolio_id(conn) -> str` (기본 `"default"`); `resolve_symbol_map(conn, asset_ids) -> dict[str,tuple[str|None,str|None]]`; 도메인 빌더는 Task 4–8에서 추가.

- [ ] **Step 1: 실패 테스트** — `tests/test_web_pages.py`

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_web_pages.py -v`
Expected: FAIL — `ModuleNotFoundError: croesus.web.cache`.

- [ ] **Step 3: cache.py 구현** — `croesus/web/cache.py`

```python
from __future__ import annotations
import threading
import time
from typing import Any, Callable


class TTLCache:
    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._store: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_set(self, key: Any, factory: Callable[[], Any]) -> Any:
        now = time.monotonic()
        with self._lock:
            hit = self._store.get(key)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        value = factory()
        with self._lock:
            self._store[key] = (time.monotonic(), value)
        return value

    def invalidate(self) -> None:
        with self._lock:
            self._store.clear()
```

> `time.monotonic()`은 테스트에서 사용 가능(스크립트 제약은 워크플로 스크립트에만 적용, 일반 런타임 코드 아님).

- [ ] **Step 4: viewmodels.py 구현** — `croesus/web/viewmodels.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Badge:
    label: str
    value: str
    tone: str = "neutral"  # ok | warn | bad | neutral


@dataclass(frozen=True)
class MacroView:
    date: date | None
    regime: str
    positioning: str
    regime_confidence: float
    amplifier_score: float
    confirmation_score: float
    warnings: list[dict] = field(default_factory=list)
    opportunities: list[dict] = field(default_factory=list)
    regime_methods: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ScreeningRow:
    rank: int | None
    symbol: str
    name: str | None
    score: float | None
    decision_bucket: str
    reason: str
    factor_scores: dict


@dataclass(frozen=True)
class ScreeningView:
    run_id: str | None
    as_of_date: date | None
    rows: list[ScreeningRow] = field(default_factory=list)


@dataclass(frozen=True)
class PortfolioView:
    as_of_date: date | None
    total_market_value: float | None
    unrealized_pnl: float | None
    holdings: list[dict] = field(default_factory=list)
    exposures: list[dict] = field(default_factory=list)
    drifts: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class OpportunityRow:
    asset_id: str
    symbol: str
    name: str | None
    current_price: float | None
    base_upside_pct: float | None
    bands: dict
    grades: dict
    confidence: str | None
    gate_status: str | None = None          # 'pass' | 'warn' | 'block' (Phase E)
    gate_reason_codes: list = field(default_factory=list)
    gate_notes: list = field(default_factory=list)


@dataclass(frozen=True)
class OpportunityView:
    as_of_date: date | None
    rows: list[OpportunityRow] = field(default_factory=list)
    gate_summary: dict | None = None        # {'pass': N, 'warn': N, 'block': N} (Phase E)


@dataclass(frozen=True)
class HomeView:
    macro: Badge | None
    actions: list[dict]
    action_count: int
    opportunity_count: int
    drift_alerts: list[str]
    screening_count: int
    freshness: list[Badge] = field(default_factory=list)
```

- [ ] **Step 5: services.py 공통부 구현** — `croesus/web/services.py`

```python
from __future__ import annotations
import duckdb

from croesus.web.cache import TTLCache

DEFAULT_PORTFOLIO_ID = "default"
opportunity_cache = TTLCache(ttl_seconds=60.0)


def resolve_portfolio_id(conn: duckdb.DuckDBPyConnection) -> str:
    row = conn.execute(
        "SELECT portfolio_id FROM portfolios ORDER BY created_at LIMIT 1"
    ).fetchone()
    return row[0] if row else DEFAULT_PORTFOLIO_ID


def resolve_symbol_map(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, tuple[str | None, str | None]]:
    if not asset_ids:
        return {}
    placeholders = ",".join(["?"] * len(asset_ids))
    rows = conn.execute(
        f"SELECT asset_id, symbol, name FROM assets WHERE asset_id IN ({placeholders})",
        asset_ids,
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_web_pages.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: 커밋**

```bash
git add croesus/web/viewmodels.py croesus/web/cache.py croesus/web/services.py tests/test_web_pages.py
git commit -m "✨ feat: web viewmodels, TTL cache, shared service resolvers"
```

---

### Task 4: 매크로 페이지 (읽기 + 라인/게이지 차트)

**Files:**
- Modify: `croesus/web/services.py` (`build_macro_view`)
- Create: `croesus/web/routes/macro.py`, `croesus/web/templates/macro.html`
- Modify: `croesus/web/app.py` (라우터 등록)
- Test: `tests/test_web_pages.py`

**Interfaces:**
- Consumes: `load_latest_macro_state(conn)` (`croesus.macro._loader`) → `MacroState`; 히스토리는 `macro_scores` 직접 쿼리.
- Produces: `services.build_macro_view(conn) -> MacroView | None`. 라우트 `GET /macro`.

- [ ] **Step 1: 실패 테스트** — `tests/test_web_pages.py`에 추가

```python
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
    assert "Goldilocks" in resp.text
    assert "Aggressive" in resp.text
```

> 패턴: 라우트 모듈이 import한 `build_macro_view`와 `get_read_connection`을 패치해 DB 없이 렌더만 검증.

- [ ] **Step 2: 실패 확인**

Run: `pytest tests/test_web_pages.py::test_macro_page_renders -v`
Expected: FAIL — `croesus.web.routes.macro` 없음.

- [ ] **Step 3: build_macro_view 구현** — `croesus/web/services.py`에 추가

```python
from croesus.macro._loader import load_latest_macro_state
from croesus.web.viewmodels import MacroView


def build_macro_view(conn) -> MacroView | None:
    state = load_latest_macro_state(conn)
    if state is None:
        return None
    rows = conn.execute(
        "SELECT date, regime, positioning, amplifier_score, confirmation_score "
        "FROM macro_scores ORDER BY date DESC LIMIT 90"
    ).fetchall()
    history = [
        {"date": str(r[0]), "regime": r[1], "positioning": r[2],
         "amplifier_score": r[3], "confirmation_score": r[4]}
        for r in reversed(rows)
    ]
    return MacroView(
        date=state.date, regime=state.regime, positioning=state.positioning,
        regime_confidence=state.regime_confidence, amplifier_score=state.amplifier_score,
        confirmation_score=state.confirmation_score, warnings=state.warnings,
        opportunities=state.opportunities, regime_methods=state.regime_methods,
        history=history,
    )
```

- [ ] **Step 4: 라우트 구현** — `croesus/web/routes/macro.py`

```python
from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_macro_view

router = APIRouter()


@router.get("/macro", response_class=HTMLResponse)
def macro(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_macro_view(conn)
    history_json = json.dumps(view.history) if view else "[]"
    return templates.TemplateResponse(
        request, "macro.html",
        {"title": "매크로", "view": view, "history_json": history_json},
    )
```

`croesus/web/app.py`에서 `from croesus.web.routes import home, macro` 후 `app.include_router(macro.router)` 추가.

- [ ] **Step 5: macro.html 작성** — `croesus/web/templates/macro.html`

```html
{% extends "base.html" %}
{% block content %}
<h1>매크로</h1>
{% if not view %}
  <section class="empty"><p>아직 매크로 데이터가 없습니다.</p></section>
{% else %}
<div class="grid grid-macro">
  <div class="card">
    <span class="muted">레짐</span>
    <strong class="regime regime-{{ view.regime|lower }}">{{ view.regime }}</strong>
    <span class="muted">포지셔닝: {{ view.positioning }}</span>
    <span class="muted">신뢰도: {{ '%.0f' % (view.regime_confidence * 100) }}%</span>
  </div>
  <div class="card"><span class="muted">Amplifier</span>
    <strong>{{ '%.0f' % view.amplifier_score }}</strong></div>
  <div class="card"><span class="muted">Confirmation</span>
    <strong>{{ '%+.2f' % view.confirmation_score }}</strong></div>
  <div class="card chart desktop-only" data-chart="macro-history"
       data-series='{{ history_json }}' style="min-height:260px"></div>
</div>
{% if view.warnings %}
<h2>경고</h2><ul>{% for w in view.warnings %}
  <li>{{ w.get('indicator','') }} — {{ w.get('code','') }}</li>{% endfor %}</ul>
{% endif %}
{% endif %}
{% endblock %}
```

- [ ] **Step 6: 통과 확인**

Run: `pytest tests/test_web_pages.py::test_macro_page_renders -v`
Expected: PASS.

- [ ] **Step 7: 커밋**

```bash
git add croesus/web/services.py croesus/web/routes/macro.py croesus/web/templates/macro.html croesus/web/app.py tests/test_web_pages.py
git commit -m "✨ feat: macro dashboard page with regime cards and history series"
```

---

### Task 5: 스크리닝 페이지 (읽기 + 랭킹 막대 + bucket 필터)

**Files:**
- Modify: `croesus/web/services.py` (`build_screening_view`)
- Create: `croesus/web/routes/screening.py`, `croesus/web/templates/screening.html`, `croesus/web/templates/partials/screening_table.html`
- Modify: `croesus/web/app.py`
- Test: `tests/test_web_pages.py`

**Interfaces:**
- Consumes: `ScreeningRepository(conn).list_results(run_id)`; 최신 run_id = `SELECT run_id FROM screening_results GROUP BY run_id ORDER BY run_id DESC LIMIT 1`; `resolve_symbol_map`.
- Produces: `services.build_screening_view(conn, bucket: str | None = None) -> ScreeningView`. 라우트 `GET /screening?bucket=`.

- [ ] **Step 1: 실패 테스트**

```python
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
```

- [ ] **Step 2: 실패 확인** — Run: `pytest tests/test_web_pages.py::test_screening_page_renders -v` → FAIL.

- [ ] **Step 3: build_screening_view 구현** — `services.py`에 추가

```python
from croesus.screening.repository import ScreeningRepository
from croesus.web.viewmodels import ScreeningView, ScreeningRow


def _latest_screening_run_id(conn) -> str | None:
    row = conn.execute(
        "SELECT run_id FROM screening_results GROUP BY run_id ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def build_screening_view(conn, bucket: str | None = None) -> ScreeningView:
    run_id = _latest_screening_run_id(conn)
    if run_id is None:
        return ScreeningView(run_id=None, as_of_date=None, rows=[])
    candidates = ScreeningRepository(conn).list_results(run_id)
    symbols = resolve_symbol_map(conn, [c.asset_id for c in candidates])
    rows = []
    for c in candidates:
        if bucket and c.decision_bucket != bucket:
            continue
        sym, name = symbols.get(c.asset_id, (c.asset_id, None))
        rows.append(ScreeningRow(rank=c.rank, symbol=sym or c.asset_id, name=name,
            score=c.score, decision_bucket=c.decision_bucket, reason=c.reason,
            factor_scores=c.factor_scores))
    as_of = None
    parts = run_id.split("-")
    if len(parts) >= 4:
        from datetime import date as _date
        try:
            as_of = _date.fromisoformat("-".join(parts[1:4]))
        except ValueError:
            as_of = None
    return ScreeningView(run_id=run_id, as_of_date=as_of, rows=rows)
```

- [ ] **Step 4: 라우트 + 템플릿**

`croesus/web/routes/screening.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_screening_view

router = APIRouter()


@router.get("/screening", response_class=HTMLResponse)
def screening(request: Request, bucket: str | None = None, db_path=Depends(get_db_path)):
    with get_read_connection(db_path) as conn:
        view = build_screening_view(conn, bucket)
    template = "partials/screening_table.html" if request.headers.get("hx-request") else "screening.html"
    return templates.TemplateResponse(request, template, {"title": "스크리닝", "view": view, "bucket": bucket})
```

`croesus/web/templates/screening.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>스크리닝</h1>
{% if view.run_id %}<p class="muted">최신 run: {{ view.run_id }}</p>{% endif %}
<div class="filters">
  {% for b in ["", "shortlist", "watch", "blocked_by_portfolio_fit"] %}
  <a hx-get="/screening{% if b %}?bucket={{ b }}{% endif %}" hx-target="#screening-table"
     class="chip{% if bucket==b or (not bucket and not b) %} active{% endif %}">{{ b or "전체" }}</a>
  {% endfor %}
</div>
<div id="screening-table">{% include "partials/screening_table.html" %}</div>
{% endblock %}
```

`croesus/web/templates/partials/screening_table.html`:

```html
{% if not view.rows %}<p class="empty">표시할 후보가 없습니다.</p>{% else %}
<table class="data">
  <thead><tr><th>#</th><th>심볼</th><th>점수</th><th>버킷</th><th>사유</th></tr></thead>
  <tbody>{% for r in view.rows %}
    <tr><td>{{ r.rank or '' }}</td><td>{{ r.symbol }}</td>
      <td>{{ '%.2f' % r.score if r.score is not none else '' }}</td>
      <td><span class="bucket bucket-{{ r.decision_bucket }}">{{ r.decision_bucket }}</span></td>
      <td class="muted">{{ r.reason }}</td></tr>{% endfor %}</tbody>
</table>{% endif %}
```

`app.py`에 `screening` 라우터 등록.

- [ ] **Step 5: 통과 확인** — Run: `pytest tests/test_web_pages.py::test_screening_page_renders -v` → PASS.

- [ ] **Step 6: 커밋**

```bash
git add croesus/web/services.py croesus/web/routes/screening.py croesus/web/templates/screening.html croesus/web/templates/partials/screening_table.html croesus/web/app.py tests/test_web_pages.py
git commit -m "✨ feat: screening page with bucket filter (HTMX) and ranking table"
```

---

### Task 6: 포트폴리오 읽기 페이지 (보유·익스포저·드리프트·제안 액션 + 도넛)

**Files:**
- Modify: `croesus/web/services.py` (`build_portfolio_view`)
- Create: `croesus/web/routes/portfolio.py`, `croesus/web/templates/portfolio.html`
- Modify: `croesus/web/app.py`
- Test: `tests/test_web_pages.py`

**Interfaces:**
- Consumes: `PortfolioRepository(conn)` — `get_holdings`, `get_exposures`, `get_drifts`, `get_snapshot`, `load_latest_rebalance_run`; `resolve_portfolio_id`, `resolve_symbol_map`. 최신 보유 날짜는 `SELECT max(as_of_date) FROM portfolio_holdings WHERE portfolio_id=?`.
- Produces: `services.build_portfolio_view(conn) -> PortfolioView`. 라우트 `GET /portfolio`.

- [ ] **Step 1: 실패 테스트**

```python
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
```

- [ ] **Step 2: 실패 확인** — FAIL (라우트 없음).

- [ ] **Step 3: build_portfolio_view 구현** — `services.py`에 추가

```python
from croesus.portfolio.repository import PortfolioRepository
from croesus.web.viewmodels import PortfolioView


def build_portfolio_view(conn) -> PortfolioView:
    pid = resolve_portfolio_id(conn)
    repo = PortfolioRepository(conn)
    row = conn.execute(
        "SELECT max(as_of_date) FROM portfolio_holdings WHERE portfolio_id = ?", [pid]
    ).fetchone()
    as_of = row[0] if row else None
    if as_of is None:
        return PortfolioView(as_of_date=None, total_market_value=None, unrealized_pnl=None)
    holdings = repo.get_holdings(pid, as_of)
    exposures = repo.get_exposures(pid, as_of)
    drifts = repo.get_drifts(pid, as_of)
    snapshot = repo.get_snapshot(pid, as_of) or {}
    run = repo.load_latest_rebalance_run(pid) or {}
    actions = run.get("actions", [])
    symbols = resolve_symbol_map(conn, [h.asset_id for h in holdings])
    total_mv = snapshot.get("total_market_value")
    h_rows = []
    for h in holdings:
        sym, name = symbols.get(h.asset_id, (h.asset_id, None))
        weight = (h.market_value / total_mv) if (h.market_value and total_mv) else None
        h_rows.append({"symbol": sym or h.asset_id, "name": name, "quantity": h.quantity,
                       "market_value": h.market_value, "currency": h.currency, "weight": weight})
    e_rows = [{"exposure_type": e.exposure_type, "exposure_name": e.exposure_name,
               "weight": e.weight, "limit_weight": e.limit_weight,
               "is_violation": e.is_violation} for e in exposures]
    d_rows = [{"sleeve_name": d.sleeve_name, "current_weight": d.current_weight,
               "target_weight": d.target_weight, "drift": d.drift,
               "is_outside_band": d.is_outside_band} for d in drifts]
    a_rows = [{"action_type": a.action_type, "human_readable_reason": a.human_readable_reason,
               "reason_codes": a.reason_codes, "estimated_trade_value": a.estimated_trade_value,
               "asset_id": a.asset_id, "sleeve_name": a.sleeve_name} for a in actions]
    return PortfolioView(as_of_date=as_of, total_market_value=total_mv,
        unrealized_pnl=snapshot.get("unrealized_pnl"), holdings=h_rows,
        exposures=e_rows, drifts=d_rows, actions=a_rows)
```

- [ ] **Step 4: 라우트 + 템플릿**

`croesus/web/routes/portfolio.py`:

```python
from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_portfolio_view

router = APIRouter()


@router.get("/portfolio", response_class=HTMLResponse)
def portfolio(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_portfolio_view(conn)
    donut = json.dumps([{"name": h["symbol"], "value": h["market_value"] or 0}
                        for h in view.holdings])
    return templates.TemplateResponse(request, "portfolio.html",
        {"title": "포트폴리오", "view": view, "donut_json": donut})
```

`croesus/web/templates/portfolio.html`:

```html
{% extends "base.html" %}
{% block content %}
<div class="page-head"><h1>포트폴리오</h1>
  <a class="btn" href="/portfolio/edit">보유 편집</a>
  <a class="btn" href="/portfolio/transactions">거래 원장</a></div>
{% if not view.as_of_date %}
  <section class="empty"><p>아직 보유 데이터가 없습니다. <a href="/portfolio/edit">보유를 입력</a>하세요.</p></section>
{% else %}
<p class="muted">기준일 {{ view.as_of_date }} · 평가액 {{ '{:,.0f}'.format(view.total_market_value or 0) }}
  · 평가손익 {{ '{:,.0f}'.format(view.unrealized_pnl or 0) }}</p>

<h2>추천 행동</h2>
{% if view.actions %}<ul class="actions">{% for a in view.actions %}
  <li><span class="action action-{{ a.action_type }}">{{ a.action_type }}</span>
    {{ a.human_readable_reason }}
    <small class="muted">[{{ a.reason_codes|join(', ') }}]</small></li>{% endfor %}</ul>
{% else %}<p class="muted">현재 제안된 행동이 없습니다.</p>{% endif %}

<div class="grid">
  <div class="card chart" data-chart="donut" data-series='{{ donut_json }}' style="min-height:260px"></div>
  <div class="card"><h2>익스포저</h2><table class="data">
    <thead><tr><th>유형</th><th>이름</th><th>비중</th><th>한도</th></tr></thead>
    <tbody>{% for e in view.exposures %}
      <tr class="{{ 'violation' if e.is_violation }}"><td>{{ e.exposure_type }}</td>
        <td>{{ e.exposure_name }}</td><td>{{ '%.1f%%' % (e.weight*100) }}</td>
        <td>{{ ('%.1f%%' % (e.limit_weight*100)) if e.limit_weight else '—' }}</td></tr>
    {% endfor %}</tbody></table></div>
</div>

<h2>정책 드리프트</h2><table class="data">
  <thead><tr><th>슬리브</th><th>현재</th><th>타깃</th><th>드리프트</th></tr></thead>
  <tbody>{% for d in view.drifts %}
    <tr class="{{ 'outside' if d.is_outside_band }}"><td>{{ d.sleeve_name }}</td>
      <td>{{ '%.1f%%' % (d.current_weight*100) }}</td><td>{{ '%.1f%%' % (d.target_weight*100) }}</td>
      <td>{{ '%+.1f%%' % (d.drift*100) }}</td></tr>{% endfor %}</tbody></table>

<h2>보유</h2><table class="data">
  <thead><tr><th>심볼</th><th>수량</th><th>평가액</th><th>비중</th></tr></thead>
  <tbody>{% for h in view.holdings %}
    <tr><td>{{ h.symbol }}</td><td>{{ h.quantity }}</td>
      <td>{{ '{:,.0f}'.format(h.market_value or 0) }}</td>
      <td>{{ ('%.1f%%' % (h.weight*100)) if h.weight else '—' }}</td></tr>{% endfor %}</tbody></table>
{% endif %}
{% endblock %}
```

`app.py`에 `portfolio` 라우터 등록.

- [ ] **Step 5: 통과 확인** — Run: `pytest tests/test_web_pages.py::test_portfolio_page_renders -v` → PASS.

- [ ] **Step 6: 커밋**

```bash
git add croesus/web/services.py croesus/web/routes/portfolio.py croesus/web/templates/portfolio.html croesus/web/app.py tests/test_web_pages.py
git commit -m "✨ feat: portfolio page with holdings, exposures, drifts, proposed actions"
```

---

### Task 7: 기회 페이지 + 상세 (TTL 캐시 + 밴드/등급 + Phase E risk-gate)

**Files:**
- Modify: `croesus/web/services.py` (`build_opportunity_view`, `build_opportunity_detail`)
- Create: `croesus/web/routes/opportunity.py`, `croesus/web/templates/opportunities.html`, `croesus/web/templates/opportunity_detail.html`
- Modify: `croesus/web/app.py`
- Test: `tests/test_web_pages.py`

**Interfaces:**
- Consumes (Phase E, PR #49): `run_opportunity_review(conn, *, methodology_key="moat_adjusted_intrinsic_value", as_of_date=None, limit=20, portfolio_id="default", profile_id="default", apply_risk_gate=True, min_liquidity_usd=...)`. 게이팅 **기본 ON** → 각 `OpportunityCard`에 `card.risk_gate: RiskGateVerdict | None`(`status` ∈ {'pass','warn','block'}, `reason_codes: list[str]`, `notes: list[str]`), 결과에 `result.gate_summary: dict[str,int] | None`. `resolve_portfolio_id`, `opportunity_cache`(Task 3).
- Produces: `services.build_opportunity_view(conn) -> OpportunityView`(`gate_summary` 포함); `services.build_opportunity_detail(conn, asset_id) -> OpportunityRow | None`. 라우트 `GET /opportunities?gate=`, `GET /opportunities/{asset_id}`.

- [ ] **Step 1: 실패 테스트**

```python
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
    assert "SECTOR_OVER_MAX" in resp.text      # 게이트 reason code 표시
    assert "block" in resp.text                # 게이트 상태 배지
```

- [ ] **Step 2: 실패 확인** — FAIL.

- [ ] **Step 3: 서비스 구현** — `services.py`에 추가

```python
from croesus.opportunities.review import run_opportunity_review
from croesus.web.viewmodels import OpportunityView, OpportunityRow

_OPP_METHODOLOGY = "moat_adjusted_intrinsic_value"


def _card_to_row(card) -> OpportunityRow:
    gate = card.risk_gate  # Phase E: RiskGateVerdict | None
    return OpportunityRow(
        asset_id=card.asset_id, symbol=card.symbol, name=card.name,
        current_price=card.current_price, base_upside_pct=card.base_upside_pct,
        bands=card.band_intrinsic_by_scenario,
        grades={"moat": card.moat_grade, "tech": card.tech_grade,
                "sector": card.sector_grade, "disruption": card.disruption_grade},
        confidence=card.thesis_confidence,
        gate_status=(gate.status if gate else None),
        gate_reason_codes=(list(gate.reason_codes) if gate else []),
        gate_notes=(list(gate.notes) if gate else []))


def build_opportunity_view(conn, gate: str | None = None) -> OpportunityView:
    pid = resolve_portfolio_id(conn)

    def factory():
        result = run_opportunity_review(
            conn, methodology_key=_OPP_METHODOLOGY,
            portfolio_id=pid, profile_id="default", apply_risk_gate=True)
        return OpportunityView(
            as_of_date=result.as_of_date,
            rows=[_card_to_row(c) for c in result.cards],
            gate_summary=getattr(result, "gate_summary", None))
    view = opportunity_cache.get_or_set((_OPP_METHODOLOGY, pid, "view"), factory)
    if gate:  # 게이트 상태 필터(캐시된 전체에서 파생)
        rows = [r for r in view.rows if r.gate_status == gate]
        return OpportunityView(as_of_date=view.as_of_date, rows=rows,
                               gate_summary=view.gate_summary)
    return view


def build_opportunity_detail(conn, asset_id: str):
    view = build_opportunity_view(conn)
    for row in view.rows:
        if row.asset_id == asset_id:
            return row
    return None
```

- [ ] **Step 4: 라우트 + 템플릿**

`croesus/web/routes/opportunity.py`:

```python
from __future__ import annotations
import json
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_opportunity_view, build_opportunity_detail

router = APIRouter()


@router.get("/opportunities", response_class=HTMLResponse)
def opportunities(request: Request, gate: str | None = None, db_path=Depends(get_db_path)):
    with get_read_connection(db_path) as conn:
        view = build_opportunity_view(conn, gate)
    scatter = json.dumps([{"symbol": r.symbol, "upside": r.base_upside_pct or 0,
                           "confidence": r.confidence or "", "gate": r.gate_status or "none"}
                          for r in view.rows])
    return templates.TemplateResponse(request, "opportunities.html",
        {"title": "기회", "view": view, "scatter_json": scatter, "gate": gate})


@router.get("/opportunities/{asset_id}", response_class=HTMLResponse)
def opportunity_detail(request: Request, asset_id: str, db_path=Depends(get_db_path)):
    with get_read_connection(db_path) as conn:
        row = build_opportunity_detail(conn, asset_id)
    bands = json.dumps(row.bands) if row else "{}"
    return templates.TemplateResponse(request, "opportunity_detail.html",
        {"title": row.symbol if row else "기회", "row": row, "bands_json": bands})
```

`croesus/web/templates/opportunities.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>기회</h1>
{% if view.gate_summary %}
<div class="gate-summary">
  <span class="badge badge-ok">pass {{ view.gate_summary.get('pass', 0) }}</span>
  <span class="badge badge-warn">warn {{ view.gate_summary.get('warn', 0) }}</span>
  <span class="badge badge-bad">block {{ view.gate_summary.get('block', 0) }}</span>
</div>
<div class="filters">
  {% for g in ["", "pass", "warn", "block"] %}
  <a href="/opportunities{% if g %}?gate={{ g }}{% endif %}"
     class="chip{% if gate==g or (not gate and not g) %} active{% endif %}">{{ g or "전체" }}</a>
  {% endfor %}
</div>
{% endif %}
{% if not view.rows %}<section class="empty"><p>표시할 기회 후보가 없습니다.</p></section>{% else %}
<p class="muted">기준일 {{ view.as_of_date }} · 추천 전용(게이트는 매매를 제안하지 않습니다)</p>
<div class="card chart desktop-only" data-chart="scatter" data-series='{{ scatter_json }}'
     style="min-height:280px"></div>
<div class="grid grid-cards">{% for r in view.rows %}
  <a class="card opp" href="/opportunities/{{ r.asset_id }}">
    <div class="opp-head"><strong>{{ r.symbol }}</strong>
      {% if r.gate_status %}<span class="gate gate-{{ r.gate_status }}">{{ r.gate_status }}</span>{% endif %}</div>
    <span class="muted">{{ r.name or '' }}</span>
    <div>현재가 {{ '{:,.0f}'.format(r.current_price or 0) }}</div>
    <div class="upside {{ 'pos' if (r.base_upside_pct or 0) > 0 else 'neg' }}">
      업사이드 {{ ('%+.0f%%' % (r.base_upside_pct*100)) if r.base_upside_pct is not none else '—' }}</div>
    {% if r.gate_reason_codes %}<div class="muted small">[{{ r.gate_reason_codes|join(', ') }}]</div>{% endif %}
    <div class="grades">{% for k,v in r.grades.items() if v %}
      <span class="grade grade-{{ v|lower }}">{{ k }}:{{ v }}</span>{% endfor %}</div>
  </a>{% endfor %}</div>
{% endif %}
{% endblock %}
```

`croesus/web/templates/opportunity_detail.html`:

```html
{% extends "base.html" %}
{% block content %}
{% if not row %}<section class="empty"><p>해당 자산을 찾을 수 없습니다.</p></section>{% else %}
<h1>{{ row.symbol }} <span class="muted">{{ row.name or '' }}</span>
  {% if row.gate_status %}<span class="gate gate-{{ row.gate_status }}">{{ row.gate_status }}</span>{% endif %}</h1>
<div class="card chart" data-chart="bands" data-series='{{ bands_json }}'
     data-price="{{ row.current_price or 0 }}" style="min-height:260px"></div>
<div class="grades">{% for k,v in row.grades.items() if v %}
  <span class="grade grade-{{ v|lower }}">{{ k }}: {{ v }}</span>{% endfor %}</div>
<p>확신도: {{ row.confidence or '—' }}</p>
{% if row.gate_notes %}<h2>리스크 게이트</h2>
  {% if row.gate_reason_codes %}<p class="muted">[{{ row.gate_reason_codes|join(', ') }}]</p>{% endif %}
  <ul>{% for n in row.gate_notes %}<li>{{ n }}</li>{% endfor %}</ul>{% endif %}
{% endif %}
{% endblock %}
```

`app.py`에 `opportunity` 라우터 등록.

- [ ] **Step 5: 통과 확인** — Run: `pytest tests/test_web_pages.py::test_opportunities_page_renders_with_gate -v` → PASS.

- [ ] **Step 6: 커밋**

```bash
git add croesus/web/services.py croesus/web/routes/opportunity.py croesus/web/templates/opportunities.html croesus/web/templates/opportunity_detail.html croesus/web/app.py tests/test_web_pages.py
git commit -m "✨ feat: opportunities page with Phase E risk-gate verdicts, summary, filter"
```

---

### Task 8: 홈 집계 (추천 행동 카드 + 배지)

**Files:**
- Modify: `croesus/web/services.py` (`build_home_view`)
- Modify: `croesus/web/routes/home.py`, `croesus/web/templates/home.html`
- Test: `tests/test_web_pages.py`

**Interfaces:**
- Consumes: `build_macro_view`, `build_portfolio_view`, `build_opportunity_view`, `build_screening_view`(모두 services).
- Produces: `services.build_home_view(conn) -> HomeView`.

- [ ] **Step 1: 실패 테스트**

```python
def test_home_aggregates(monkeypatch):
    from croesus.web.viewmodels import HomeView, Badge
    hv = HomeView(macro=Badge("레짐","Goldilocks","ok"),
        actions=[{"action_type":"trim","human_readable_reason":"섹터 과다"}],
        action_count=1, opportunity_count=3, drift_alerts=["core_us_equity 밴드 이탈"],
        screening_count=12, freshness=[Badge("매크로","2026-06-22","ok")])
    monkeypatch.setattr("croesus.web.routes.home.build_home_view", lambda conn: hv)
    monkeypatch.setattr("croesus.web.routes.home.get_read_connection",
                        __import__("contextlib").contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("storage/croesus.duckdb"), raise_server_exceptions=False)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "섹터 과다" in resp.text and "Goldilocks" in resp.text
```

- [ ] **Step 2: 실패 확인** — FAIL (home이 build_home_view 미사용).

- [ ] **Step 3: build_home_view 구현** — `services.py`에 추가

```python
from croesus.web.viewmodels import HomeView, Badge


def build_home_view(conn) -> HomeView:
    macro = build_macro_view(conn)
    portfolio = build_portfolio_view(conn)
    opps = build_opportunity_view(conn)
    screening = build_screening_view(conn)
    macro_badge = (Badge("레짐", f"{macro.regime} · {macro.positioning}", "ok")
                   if macro else None)
    drift_alerts = [f"{d['sleeve_name']} 밴드 이탈" for d in portfolio.drifts
                    if d.get("is_outside_band")]
    drift_alerts += [f"{e['exposure_name']} 한도 초과" for e in portfolio.exposures
                     if e.get("is_violation")]
    freshness = []
    if macro and macro.date:
        freshness.append(Badge("매크로", str(macro.date), "ok"))
    if portfolio.as_of_date:
        freshness.append(Badge("포트폴리오", str(portfolio.as_of_date), "ok"))
    if screening.as_of_date:
        freshness.append(Badge("스크리닝", str(screening.as_of_date), "ok"))
    return HomeView(macro=macro_badge, actions=portfolio.actions[:3],
        action_count=len(portfolio.actions), opportunity_count=len(opps.rows),
        drift_alerts=drift_alerts, screening_count=len(screening.rows), freshness=freshness)
```

- [ ] **Step 4: home 라우트 + 템플릿 갱신**

`croesus/web/routes/home.py` 교체:

```python
from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection
from croesus.web.services import build_home_view

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_home_view(conn)
    return templates.TemplateResponse(request, "home.html",
        {"title": "오늘 한눈에", "view": view})
```

`croesus/web/templates/home.html` 교체:

```html
{% extends "base.html" %}
{% block content %}
<h1>오늘 한눈에</h1>
<section class="card actions-card">
  <div class="card-head"><h2>오늘의 추천 행동</h2>
    <a href="/portfolio" class="muted">전체 {{ view.action_count }}건 →</a></div>
  {% if view.actions %}<ul class="actions">{% for a in view.actions %}
    <li><span class="action action-{{ a.action_type }}">{{ a.action_type }}</span>
      {{ a.human_readable_reason }}</li>{% endfor %}</ul>
  {% else %}<p class="muted">현재 제안된 행동이 없습니다.</p>{% endif %}
</section>
<div class="grid grid-badges">
  {% if view.macro %}<a class="card" href="/macro"><span class="muted">{{ view.macro.label }}</span>
    <strong>{{ view.macro.value }}</strong></a>{% endif %}
  <a class="card" href="/opportunities"><span class="muted">기회 후보</span>
    <strong>{{ view.opportunity_count }}</strong></a>
  <a class="card" href="/screening"><span class="muted">스크리닝 숏리스트</span>
    <strong>{{ view.screening_count }}</strong></a>
</div>
{% if view.drift_alerts %}<section class="card alerts"><h2>경보</h2>
  <ul>{% for a in view.drift_alerts %}<li>{{ a }}</li>{% endfor %}</ul></section>{% endif %}
<div class="freshness">{% for b in view.freshness %}
  <span class="badge badge-{{ b.tone }}">{{ b.label }} {{ b.value }}</span>{% endfor %}</div>
{% endblock %}
```

- [ ] **Step 5: 통과 확인** — Run: `pytest tests/test_web_pages.py::test_home_aggregates -v` → PASS. 전체: `pytest tests/test_web_pages.py -v`.

- [ ] **Step 6: 커밋**

```bash
git add croesus/web/services.py croesus/web/routes/home.py croesus/web/templates/home.html tests/test_web_pages.py
git commit -m "✨ feat: home overview with recommended-actions card and freshness badges"
```

---

### Task 9: 프로필 편집 (GET/POST + 검증)

**Files:**
- Create: `croesus/web/forms.py`, `croesus/web/routes/settings.py`, `croesus/web/templates/settings_profile.html`, `croesus/web/templates/partials/form_errors.html`
- Modify: `croesus/web/app.py`
- Test: `tests/test_web_forms.py`, `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `ProfileRepository(conn)` — `get_profile`, `save_profile`; `validate_profile`, `validate_policy_targets` (`croesus.profiles.validation`); `InvestorProfile`, `PolicyTarget`, `Currency`, `TradeMode` (`croesus.profiles.models`); `get_write_connection`.
- Produces: `forms.parse_profile_form(form: dict, existing: InvestorProfile) -> tuple[InvestorProfile, list[PolicyTarget], list[str]]` (errors 비어있으면 유효). 라우트 `GET/POST /settings/profile`.

- [ ] **Step 1: 실패 테스트 (forms 순수함수)** — `tests/test_web_forms.py`

```python
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
    assert errors
```

- [ ] **Step 2: 실패 확인** — Run: `pytest tests/test_web_forms.py -v` → FAIL.

- [ ] **Step 3: forms.py 구현** — `croesus/web/forms.py`

```python
from __future__ import annotations
from dataclasses import replace

from croesus.profiles.models import InvestorProfile, PolicyTarget, Currency, TradeMode
from croesus.profiles.validation import validate_profile, validate_policy_targets

_FLOAT_FIELDS = [
    "expected_annual_return", "max_tolerable_drawdown", "monthly_contribution",
    "liquidity_buffer_months", "max_single_position_weight", "max_sector_weight",
    "max_industry_weight", "max_theme_weight", "max_country_weight",
    "max_currency_weight", "max_monthly_turnover", "rebalance_band",
]


def _as_list(value):
    return value if isinstance(value, list) else [value]


def parse_profile_form(form: dict, existing: InvestorProfile):
    errors: list[str] = []
    kwargs: dict = {}
    for key in _FLOAT_FIELDS:
        try:
            kwargs[key] = float(form.get(key, ""))
        except (TypeError, ValueError):
            errors.append(f"{key}: 숫자를 입력하세요")
    try:
        kwargs["investment_horizon_years"] = int(form.get("investment_horizon_years", ""))
    except (TypeError, ValueError):
        errors.append("investment_horizon_years: 정수를 입력하세요")
    try:
        kwargs["trade_mode"] = TradeMode(form.get("trade_mode", existing.trade_mode.value))
    except ValueError:
        errors.append("trade_mode: 허용되지 않는 값")

    if errors:
        return existing, [], errors

    profile = replace(existing, **kwargs)

    names = _as_list(form.get("sleeve_name", []))
    tw = _as_list(form.get("target_weight", []))
    mn = _as_list(form.get("min_weight", []))
    mx = _as_list(form.get("max_weight", []))
    targets: list[PolicyTarget] = []
    for i, name in enumerate(names):
        if not name:
            continue
        try:
            target_weight = float(tw[i])
        except (IndexError, ValueError):
            errors.append(f"{name}: 타깃 비중이 숫자가 아닙니다")
            continue
        min_w = float(mn[i]) if i < len(mn) and mn[i] not in ("", None) else None
        max_w = float(mx[i]) if i < len(mx) and mx[i] not in ("", None) else None
        targets.append(PolicyTarget(profile_id=profile.profile_id, sleeve_name=name,
            target_weight=target_weight, min_weight=min_w, max_weight=max_w, metadata={}))

    pr = validate_profile(profile)
    tr = validate_policy_targets(targets)
    errors += [str(e) for e in getattr(pr, "errors", [])]
    errors += [str(e) for e in getattr(tr, "errors", [])]
    return profile, targets, errors
```

> 검증: `ProfileValidationResult`의 에러 컬렉션 속성명을 구현 시 확인(`errors` 가정). 다르면 그 속성으로 교체.

- [ ] **Step 4: forms 통과 확인** — Run: `pytest tests/test_web_forms.py -v` → PASS (3 passed).

- [ ] **Step 5: 라우트 + 템플릿 (settings)**

`croesus/web/routes/settings.py`:

```python
from __future__ import annotations
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse

from croesus.web.deps import templates, get_db_path
from croesus.web.db import get_read_connection, get_write_connection
from croesus.web.forms import parse_profile_form
from croesus.web.services import resolve_portfolio_id
from croesus.profiles.repository import ProfileRepository
from croesus.profiles.seed_default_profile import DEFAULT_PROFILE

router = APIRouter()


def _load_profile(conn):
    repo = ProfileRepository(conn)
    try:
        existing = repo.get_profile(DEFAULT_PROFILE.profile_id)
    except Exception:
        existing = None
    return existing or DEFAULT_PROFILE


@router.get("/settings/profile", response_class=HTMLResponse)
def edit_profile(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        profile = _load_profile(conn)
        targets = ProfileRepository(conn).get_policy_targets(profile.profile_id)
    return templates.TemplateResponse(request, "settings_profile.html",
        {"title": "프로필 설정", "profile": profile, "targets": targets, "errors": []})


@router.post("/settings/profile", response_class=HTMLResponse)
async def save_profile_route(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = {k: form.getlist(k) if k in ("sleeve_name","target_weight","min_weight","max_weight")
            else form.get(k) for k in form.keys()}
    with get_read_connection(db_path) as conn:
        existing = _load_profile(conn)
    profile, targets, errors = parse_profile_form(data, existing)
    if errors:
        return templates.TemplateResponse(request, "settings_profile.html",
            {"title": "프로필 설정", "profile": profile, "targets": targets, "errors": errors},
            status_code=400)
    with get_write_connection(db_path) as conn:
        ProfileRepository(conn).save_profile(profile, targets)
    return RedirectResponse("/settings/profile", status_code=303)
```

> `ProfileRepository.get_policy_targets`의 정확한 메서드명은 구현 시 확인(없으면 `replace_policy_targets`의 짝 읽기 메서드 또는 `get_profile`가 함께 반환하는지 확인). 읽기 실패 시 빈 리스트로 폴백.

`croesus/web/templates/settings_profile.html` (요지 — 전체 필드 포함):

```html
{% extends "base.html" %}
{% block content %}
<h1>프로필 설정</h1>
{% include "partials/form_errors.html" %}
<form method="post" action="/settings/profile" class="form">
  <fieldset><legend>위험/목표</legend>
    <label>기대 연수익 <input name="expected_annual_return" value="{{ profile.expected_annual_return }}"></label>
    <label>최대 허용 드로다운(음수) <input name="max_tolerable_drawdown" value="{{ profile.max_tolerable_drawdown }}"></label>
    <label>투자기간(년) <input name="investment_horizon_years" value="{{ profile.investment_horizon_years }}"></label>
    <label>월 납입 <input name="monthly_contribution" value="{{ profile.monthly_contribution }}"></label>
    <label>유동성 버퍼(개월) <input name="liquidity_buffer_months" value="{{ profile.liquidity_buffer_months }}"></label>
  </fieldset>
  <fieldset><legend>한도</legend>
    {% for f in ["max_single_position_weight","max_sector_weight","max_industry_weight",
                 "max_theme_weight","max_country_weight","max_currency_weight",
                 "max_monthly_turnover","rebalance_band"] %}
      <label>{{ f }} <input name="{{ f }}" value="{{ profile[f] }}"></label>{% endfor %}
    <label>trade_mode
      <select name="trade_mode">
        <option value="propose_only" {{ 'selected' if profile.trade_mode.value=='propose_only' }}>propose_only</option>
        <option value="approval_required" {{ 'selected' if profile.trade_mode.value=='approval_required' }}>approval_required</option>
      </select></label>
  </fieldset>
  <fieldset><legend>슬리브 타깃 (합 = 1.0)</legend>
    <table class="data"><thead><tr><th>슬리브</th><th>타깃</th><th>최소</th><th>최대</th></tr></thead>
    <tbody>{% for t in targets %}
      <tr><td><input name="sleeve_name" value="{{ t.sleeve_name }}"></td>
        <td><input name="target_weight" value="{{ t.target_weight }}"></td>
        <td><input name="min_weight" value="{{ t.min_weight if t.min_weight is not none else '' }}"></td>
        <td><input name="max_weight" value="{{ t.max_weight if t.max_weight is not none else '' }}"></td></tr>
    {% endfor %}
      <tr><td><input name="sleeve_name" placeholder="새 슬리브"></td>
        <td><input name="target_weight"></td><td><input name="min_weight"></td>
        <td><input name="max_weight"></td></tr></tbody></table>
  </fieldset>
  <button type="submit" class="btn primary">저장</button>
</form>
{% endblock %}
```

`croesus/web/templates/partials/form_errors.html`:

```html
{% if errors %}<div class="errors">
  <strong>저장할 수 없습니다:</strong>
  <ul>{% for e in errors %}<li>{{ e }}</li>{% endfor %}</ul></div>{% endif %}
```

> 템플릿의 `profile[f]` 동적 접근을 위해 `create_app`에서 Jinja 환경에 속성 접근을 허용해야 함. 기본 `Jinja2Templates`는 `getattr`을 지원하므로 `profile[f]`가 실패하면 매크로 대신 명시 필드로 펼친다(구현 시 확인). 안전책: 위 `한도` 루프를 8개 `<label>`로 명시 작성.

`app.py`에 `settings` 라우터 등록.

- [ ] **Step 6: 라우트 테스트** — `tests/test_web_settings.py`

```python
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
```

- [ ] **Step 7: 통과 확인** — Run: `pytest tests/test_web_settings.py tests/test_web_forms.py -v` → PASS.

- [ ] **Step 8: 커밋**

```bash
git add croesus/web/forms.py croesus/web/routes/settings.py croesus/web/templates/settings_profile.html croesus/web/templates/partials/form_errors.html croesus/web/app.py tests/test_web_forms.py tests/test_web_settings.py
git commit -m "✨ feat: profile editor with validation-gated save"
```

---

### Task 10: 보유 인라인 편집 (GET/POST → CSV → 스냅샷 재계산)

**Files:**
- Modify: `croesus/web/forms.py` (`holdings_form_to_csv`)
- Modify: `croesus/web/routes/portfolio.py` (edit GET, holdings POST)
- Create: `croesus/web/templates/portfolio_edit.html`, `croesus/web/templates/partials/holdings_rows.html`
- Test: `tests/test_web_forms.py`, `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `run_portfolio_snapshot(conn, holdings_path, *, portfolio_id, as_of_date)` (`croesus.jobs.portfolio_snapshot`); `resolve_portfolio_id`; `get_write_connection`; `services.opportunity_cache`.
- Produces: `forms.holdings_form_to_csv(form: dict) -> str` (CSV 문자열, 헤더 `symbol,quantity,avg_cost,currency,market_value`). 라우트 `GET /portfolio/edit`, `POST /portfolio/holdings`.

- [ ] **Step 1: 실패 테스트 (CSV 직렬화 순수함수)** — `tests/test_web_forms.py`에 추가

```python
import csv, io
from croesus.web.forms import holdings_form_to_csv


def test_holdings_form_to_csv():
    form = {"symbol":["AAPL","CASH"], "quantity":["10",""], "avg_cost":["150",""],
            "currency":["USD","USD"], "market_value":["","500"]}
    text = holdings_form_to_csv(form)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["symbol"] == "AAPL" and rows[0]["quantity"] == "10"
    assert rows[1]["symbol"] == "CASH" and rows[1]["market_value"] == "500"


def test_holdings_form_to_csv_skips_empty_rows():
    form = {"symbol":["AAPL",""], "quantity":["10",""], "avg_cost":["150",""],
            "currency":["USD",""], "market_value":["",""]}
    text = holdings_form_to_csv(form)
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
```

- [ ] **Step 2: 실패 확인** — Run: `pytest tests/test_web_forms.py -k holdings -v` → FAIL.

- [ ] **Step 3: holdings_form_to_csv 구현** — `forms.py`에 추가

```python
import csv
import io

_HOLDINGS_HEADER = ["symbol", "quantity", "avg_cost", "currency", "market_value"]


def holdings_form_to_csv(form: dict) -> str:
    def col(name):
        v = form.get(name, [])
        return v if isinstance(v, list) else [v]
    symbols = col("symbol")
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_HOLDINGS_HEADER)
    writer.writeheader()
    for i, sym in enumerate(symbols):
        if not sym or not sym.strip():
            continue
        row = {h: (col(h)[i] if i < len(col(h)) else "") for h in _HOLDINGS_HEADER}
        row["symbol"] = sym.strip()
        writer.writerow(row)
    return out.getvalue()
```

- [ ] **Step 4: CSV 통과 확인** — Run: `pytest tests/test_web_forms.py -k holdings -v` → PASS.

- [ ] **Step 5: 라우트 추가** — `croesus/web/routes/portfolio.py`에 추가

```python
import tempfile
from pathlib import Path
from fastapi.responses import RedirectResponse

from croesus.web.db import get_write_connection
from croesus.web.forms import holdings_form_to_csv
from croesus.web.services import build_portfolio_view, resolve_portfolio_id, opportunity_cache
from croesus.jobs.portfolio_snapshot import run_portfolio_snapshot


@router.get("/portfolio/edit", response_class=HTMLResponse)
def edit_holdings(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        view = build_portfolio_view(conn)
    return templates.TemplateResponse(request, "portfolio_edit.html",
        {"title": "보유 편집", "view": view})


@router.post("/portfolio/holdings", response_class=HTMLResponse)
async def save_holdings(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = {k: form.getlist(k) for k in ("symbol","quantity","avg_cost","currency","market_value")}
    csv_text = holdings_form_to_csv(data)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
        tmp.write(csv_text)
        tmp_path = Path(tmp.name)
    try:
        with get_write_connection(db_path) as conn:
            pid = resolve_portfolio_id(conn)
            run_portfolio_snapshot(conn, tmp_path, portfolio_id=pid)
    finally:
        tmp_path.unlink(missing_ok=True)
    opportunity_cache.invalidate()
    return RedirectResponse("/portfolio", status_code=303)
```

`croesus/web/templates/portfolio_edit.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>보유 편집</h1>
<form method="post" action="/portfolio/holdings" class="form">
  <table class="data" id="holdings">
    <thead><tr><th>심볼</th><th>수량</th><th>평균단가</th><th>통화</th><th>평가액(현금)</th><th></th></tr></thead>
    <tbody>
    {% for h in view.holdings %}
      {% include "partials/holdings_rows.html" %}
    {% endfor %}
    {% set h = {"symbol":"","quantity":"","market_value":"","currency":"USD"} %}
    {% include "partials/holdings_rows.html" %}
    </tbody>
  </table>
  <button type="button" class="btn"
    hx-get="/portfolio/edit/row" hx-target="#holdings tbody" hx-swap="beforeend">행 추가</button>
  <button type="submit" class="btn primary">저장 후 재계산</button>
</form>
{% endblock %}
```

`croesus/web/templates/partials/holdings_rows.html`:

```html
<tr>
  <td><input name="symbol" value="{{ h.symbol if h.symbol is defined else '' }}" list="symbols"></td>
  <td><input name="quantity" value="{{ h.quantity if h.quantity is defined else '' }}"></td>
  <td><input name="avg_cost" value=""></td>
  <td><input name="currency" value="{{ h.currency if h.currency is defined else 'USD' }}"></td>
  <td><input name="market_value" value=""></td>
  <td><button type="button" class="btn small" onclick="this.closest('tr').remove()">삭제</button></td>
</tr>
```

추가로 "행 추가" 엔드포인트 `GET /portfolio/edit/row` (빈 행 partial 반환):

```python
@router.get("/portfolio/edit/row", response_class=HTMLResponse)
def add_holding_row(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "partials/holdings_rows.html", {"h": {}})
```

- [ ] **Step 6: 라우트 테스트** — `tests/test_web_settings.py`에 추가

```python
def test_holdings_post_recomputes(monkeypatch):
    calls = {}
    def fake_run(conn, path, *, portfolio_id, as_of_date=None):
        calls["path"] = str(path); calls["pid"] = portfolio_id
    monkeypatch.setattr("croesus.web.routes.portfolio.run_portfolio_snapshot", fake_run)
    monkeypatch.setattr("croesus.web.routes.portfolio.resolve_portfolio_id", lambda c: "default")
    monkeypatch.setattr("croesus.web.routes.portfolio.get_write_connection",
                        contextmanager(lambda p: iter([None])))
    client = TestClient(create_app("x.duckdb"), raise_server_exceptions=False)
    resp = client.post("/portfolio/holdings", data={
        "symbol":["AAPL"], "quantity":["10"], "avg_cost":["150"],
        "currency":["USD"], "market_value":[""]}, follow_redirects=False)
    assert resp.status_code == 303
    assert calls["pid"] == "default" and calls["path"].endswith(".csv")
```

> `contextmanager`는 파일 상단에서 `from contextlib import contextmanager`로 import.

- [ ] **Step 7: 통과 확인** — Run: `pytest tests/test_web_settings.py tests/test_web_forms.py -v` → PASS.

- [ ] **Step 8: 커밋**

```bash
git add croesus/web/forms.py croesus/web/routes/portfolio.py croesus/web/templates/portfolio_edit.html croesus/web/templates/partials/holdings_rows.html tests/test_web_forms.py tests/test_web_settings.py
git commit -m "✨ feat: inline holdings editor saving via snapshot recompute"
```

---

### Task 11: 거래 원장 (GET 목록/추가 폼 + POST)

**Files:**
- Modify: `croesus/web/forms.py` (`parse_transaction_form`)
- Modify: `croesus/web/routes/portfolio.py` (transactions GET/POST)
- Create: `croesus/web/templates/transactions.html`
- Test: `tests/test_web_forms.py`, `tests/test_web_settings.py`

**Interfaces:**
- Consumes: `PortfolioTransaction`, `validate_transaction` (`croesus.portfolio.transactions`); `record_manual_transaction` (`croesus.jobs.record_transaction`) 또는 `TransactionRepository.record_transaction`; `get_write_connection`.
- Produces: `forms.parse_transaction_form(form: dict, portfolio_id: str) -> tuple[PortfolioTransaction | None, list[str]]`. 라우트 `GET /portfolio/transactions`, `POST /portfolio/transactions`.

- [ ] **Step 1: 실패 테스트 (forms)** — `tests/test_web_forms.py`에 추가

```python
from croesus.web.forms import parse_transaction_form


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
```

- [ ] **Step 2: 실패 확인** — Run: `pytest tests/test_web_forms.py -k transaction -v` → FAIL.

- [ ] **Step 3: parse_transaction_form 구현** — `forms.py`에 추가

```python
from datetime import date as _date
import uuid
from croesus.portfolio.transactions import PortfolioTransaction, validate_transaction


def parse_transaction_form(form: dict, portfolio_id: str):
    errors: list[str] = []
    def num(key, default=0.0):
        raw = form.get(key)
        if raw in (None, ""):
            return default
        try:
            return float(raw)
        except ValueError:
            errors.append(f"{key}: 숫자를 입력하세요")
            return default
    try:
        txn_date = _date.fromisoformat(form.get("transaction_date", ""))
    except ValueError:
        errors.append("transaction_date: YYYY-MM-DD 형식")
        txn_date = None
    if errors:
        return None, errors
    txn = PortfolioTransaction(
        transaction_id=str(uuid.uuid4()), portfolio_id=portfolio_id,
        transaction_date=txn_date, transaction_type=form.get("transaction_type", ""),
        asset_id=form.get("asset_id") or None, quantity=num("quantity"),
        price=num("price"), gross_amount=num("gross_amount"),
        currency=form.get("currency") or "USD", fees=num("fees"),
        source="web", linked_action_id=None, metadata={})
    errors += list(validate_transaction(txn))
    if errors:
        return None, errors
    return txn, errors
```

> `uuid.uuid4()`는 일반 런타임 코드에서 사용 가능. `PortfolioTransaction` 생성자 인자명은 `transactions.py:58` 기준 — 구현 시 정확한 필드명 대조.

- [ ] **Step 4: forms 통과 확인** — Run: `pytest tests/test_web_forms.py -k transaction -v` → PASS.

- [ ] **Step 5: 라우트 + 템플릿** — `croesus/web/routes/portfolio.py`에 추가

```python
from croesus.web.forms import parse_transaction_form
from croesus.portfolio.transaction_repository import TransactionRepository


@router.get("/portfolio/transactions", response_class=HTMLResponse)
def transactions(request: Request, db_path=Depends(get_db_path)) -> HTMLResponse:
    with get_read_connection(db_path) as conn:
        pid = resolve_portfolio_id(conn)
        rows = conn.execute(
            "SELECT transaction_date, transaction_type, asset_id, quantity, price, currency "
            "FROM portfolio_transactions WHERE portfolio_id = ? ORDER BY transaction_date DESC LIMIT 100",
            [pid]).fetchall()
    ledger = [{"date": str(r[0]), "type": r[1], "asset_id": r[2], "quantity": r[3],
               "price": r[4], "currency": r[5]} for r in rows]
    return templates.TemplateResponse(request, "transactions.html",
        {"title": "거래 원장", "ledger": ledger, "errors": []})


@router.post("/portfolio/transactions", response_class=HTMLResponse)
async def add_transaction(request: Request, db_path=Depends(get_db_path)):
    form = await request.form()
    data = {k: form.get(k) for k in form.keys()}
    with get_read_connection(db_path) as conn:
        pid = resolve_portfolio_id(conn)
    txn, errors = parse_transaction_form(data, pid)
    if errors:
        return templates.TemplateResponse(request, "transactions.html",
            {"title": "거래 원장", "ledger": [], "errors": errors}, status_code=400)
    with get_write_connection(db_path) as conn:
        TransactionRepository(conn).record_transaction(txn)
    return RedirectResponse("/portfolio/transactions", status_code=303)
```

`croesus/web/templates/transactions.html`:

```html
{% extends "base.html" %}
{% block content %}
<h1>거래 원장</h1>
{% include "partials/form_errors.html" %}
<form method="post" action="/portfolio/transactions" class="form inline">
  <select name="transaction_type">
    {% for t in ["buy","sell","deposit","withdrawal","dividend","fee","manual_adjustment"] %}
    <option value="{{ t }}">{{ t }}</option>{% endfor %}</select>
  <input name="asset_id" placeholder="asset_id">
  <input name="quantity" placeholder="수량"><input name="price" placeholder="가격">
  <input name="gross_amount" placeholder="금액(입출금/배당)">
  <input name="currency" value="USD"><input name="fees" placeholder="수수료" value="0">
  <input name="transaction_date" type="date" required>
  <button type="submit" class="btn primary">기록</button>
</form>
<table class="data"><thead><tr><th>일자</th><th>유형</th><th>자산</th><th>수량</th><th>가격</th><th>통화</th></tr></thead>
  <tbody>{% for t in ledger %}<tr><td>{{ t.date }}</td><td>{{ t.type }}</td>
    <td>{{ t.asset_id or '' }}</td><td>{{ t.quantity }}</td><td>{{ t.price }}</td>
    <td>{{ t.currency }}</td></tr>{% endfor %}</tbody></table>
{% endblock %}
```

- [ ] **Step 6: 라우트 테스트** — `tests/test_web_settings.py`에 추가

```python
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
```

- [ ] **Step 7: 통과 확인** — Run: `pytest tests/test_web_settings.py tests/test_web_forms.py -v` → PASS.

- [ ] **Step 8: 커밋**

```bash
git add croesus/web/forms.py croesus/web/routes/portfolio.py croesus/web/templates/transactions.html tests/test_web_forms.py tests/test_web_settings.py
git commit -m "✨ feat: transaction ledger entry with validation"
```

---

### Task 12: 적응형 CSS + 벤더링 차트(ECharts/HTMX) + 최종 검증

**Files:**
- Modify: `croesus/web/static/css/app.css` (적응형 그리드·테마·의미 컬러)
- Replace: `croesus/web/static/js/htmx.min.js`, `croesus/web/static/js/echarts.min.js` (실제 벤더 파일)
- Modify: `croesus/web/static/js/charts.js` (data-chart 초기화)
- Modify: `README.md` (실행/Tailscale 안내)
- Test: 전체 스위트

**Interfaces:**
- Consumes: 템플릿의 `data-chart`(`macro-history`,`donut`,`scatter`,`bands`)와 `data-series` JSON.
- Produces: 벤더링 자산 + 차트 부트스트랩. 신규 파이썬 인터페이스 없음.

- [ ] **Step 1: 벤더 자산 다운로드(고정 버전)**

```bash
curl -fsSL https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js -o croesus/web/static/js/htmx.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/echarts@5.5.1/dist/echarts.min.js -o croesus/web/static/js/echarts.min.js
test -s croesus/web/static/js/htmx.min.js && test -s croesus/web/static/js/echarts.min.js && echo OK
```

Expected: `OK` (두 파일 모두 비어있지 않음).

- [ ] **Step 2: charts.js 작성** — `croesus/web/static/js/charts.js`

```javascript
function initCharts() {
  document.querySelectorAll('[data-chart]').forEach(function (el) {
    if (el.offsetParent === null) return;            // 숨김(모바일 desktop-only) 스킵
    if (el.__inited) return; el.__inited = true;
    var kind = el.getAttribute('data-chart');
    var data = JSON.parse(el.getAttribute('data-series') || '[]');
    var chart = echarts.init(el);
    var opt;
    if (kind === 'donut') {
      opt = { tooltip: {}, series: [{ type: 'pie', radius: ['45%','70%'], data: data }] };
    } else if (kind === 'macro-history') {
      opt = { tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: data.map(d => d.date) },
        yAxis: { type: 'value' },
        series: [{ type: 'line', smooth: true, data: data.map(d => d.amplifier_score) }] };
    } else if (kind === 'scatter') {
      var gateColor = { pass: '#1a7f37', warn: '#9a6700', block: '#cf222e', none: '#888' };
      opt = { tooltip: { formatter: p => p.data[3] + ' (' + p.data[2] + ')' },
        xAxis: { name: '업사이드' }, yAxis: { name: '확신도' },
        series: [{ type: 'scatter', symbolSize: 16,
          itemStyle: { color: p => gateColor[p.data[4]] || '#888' },
          data: data.map(d => [d.upside, d.confidence, d.gate, d.symbol, d.gate]) }] };
    } else if (kind === 'bands') {
      var keys = Object.keys(data);
      opt = { tooltip: {}, xAxis: { type: 'category', data: keys },
        yAxis: { type: 'value' },
        series: [{ type: 'bar', data: keys.map(k => data[k]) }] };
    }
    if (opt) chart.setOption(opt);
    window.addEventListener('resize', function () { chart.resize(); });
  });
}
window.addEventListener('DOMContentLoaded', initCharts);
document.body && document.body.addEventListener('htmx:afterSwap', initCharts);
```

- [ ] **Step 3: 적응형 CSS 작성** — `croesus/web/static/css/app.css` (Task 1 최소본 대체)

```css
:root { color-scheme: light dark; --gap: 1rem; --accent: #2d6cdf;
  --ok:#1a7f37; --warn:#9a6700; --bad:#cf222e; --muted:#6b7280; }
* { box-sizing: border-box; }
body { font-family: system-ui, -apple-system, sans-serif; margin: 0; line-height: 1.5; }
.container { padding: var(--gap); max-width: 1200px; margin: 0 auto; padding-bottom: 4rem; }
.topbar { padding: .75rem 1rem; border-bottom: 1px solid #8884; }
.brand { font-weight: 700; text-decoration: none; color: inherit; }
.muted { color: var(--muted); font-size: .9em; }
.card { border: 1px solid #8883; border-radius: 12px; padding: 1rem;
  display: flex; flex-direction: column; gap: .35rem; }
.grid { display: grid; gap: var(--gap); grid-template-columns: 1fr; }
.grid-cards, .grid-badges, .grid-macro { display: grid; gap: var(--gap); grid-template-columns: 1fr; }
table.data { width: 100%; border-collapse: collapse; font-size: .92rem; }
table.data th, table.data td { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid #8882; }
.violation, .outside { background: color-mix(in srgb, var(--bad) 12%, transparent); }
.action, .bucket, .grade, .badge, .chip { display: inline-block; padding: .1rem .5rem;
  border-radius: 999px; font-size: .8rem; border: 1px solid #8884; }
.action-trim, .badge-bad, .grade-d { color: var(--bad); }
.badge-ok, .grade-a { color: var(--ok); }
.badge-warn { color: var(--warn); }
.gate { display: inline-block; padding: .05rem .45rem; border-radius: 999px; font-size: .72rem;
  font-weight: 700; text-transform: uppercase; }
.gate-pass { background: color-mix(in srgb, var(--ok) 18%, transparent); color: var(--ok); }
.gate-warn { background: color-mix(in srgb, var(--warn) 18%, transparent); color: var(--warn); }
.gate-block { background: color-mix(in srgb, var(--bad) 18%, transparent); color: var(--bad); }
.gate-summary { display: flex; gap: .4rem; margin: .5rem 0; }
.opp-head { display: flex; justify-content: space-between; align-items: center; }
.small { font-size: .8rem; }
.btn { display: inline-block; padding: .45rem .8rem; border-radius: 8px;
  border: 1px solid #8885; text-decoration: none; color: inherit; background: transparent; cursor: pointer; }
.btn.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
.btn.small { padding: .2rem .5rem; font-size: .8rem; }
.bottomnav { position: fixed; bottom: 0; left: 0; right: 0; display: flex;
  justify-content: space-around; padding: .4rem; background: Canvas; border-top: 1px solid #8884; }
.bottomnav a { text-decoration: none; color: inherit; font-size: .8rem; padding: .3rem; }
.errors { border: 1px solid var(--bad); border-radius: 8px; padding: .75rem; margin: .5rem 0;
  background: color-mix(in srgb, var(--bad) 8%, transparent); }
.form label { display: block; margin: .4rem 0; }
.form input, .form select { padding: .35rem; }
.desktop-only { display: none; }
.card { animation: fade .25s ease; } @keyframes fade { from { opacity: 0; transform: translateY(4px);} to {opacity:1;} }

@media (min-width: 641px) {
  .grid-cards { grid-template-columns: repeat(2, 1fr); }
  .grid-badges { grid-template-columns: repeat(3, 1fr); }
  .grid-macro { grid-template-columns: repeat(2, 1fr); }
  .bottomnav { position: sticky; }
}
@media (min-width: 1024px) {
  .grid { grid-template-columns: repeat(2, 1fr); }
  .grid-cards { grid-template-columns: repeat(3, 1fr); }
  .grid-macro { grid-template-columns: repeat(4, 1fr); }
  .desktop-only { display: flex; }
}
```

- [ ] **Step 4: README에 실행/Tailscale 안내 추가** — `README.md` 끝에 섹션 추가

```markdown
## 웹 대시보드

```bash
python -m croesus.web --port 8000          # 0.0.0.0 바인딩
```

기동 시 접속 URL을 출력합니다. Tailscale이 설치돼 있으면 tailnet IP가 표시되며,
태블릿·폰에서 같은 tailnet으로 접속하면 됩니다. HTTPS가 필요하면:

```bash
tailscale serve --bg 8000
```
```

- [ ] **Step 5: 전체 테스트** 

Run: `pytest tests/test_web_app.py tests/test_web_db.py tests/test_web_pages.py tests/test_web_forms.py tests/test_web_settings.py -v`
Expected: 전부 PASS.

- [ ] **Step 6: 수동 스모크 (실데이터)**

```bash
python -m croesus.web --port 8765
```
브라우저로 `/`, `/macro`, `/screening`, `/portfolio`, `/opportunities`, `/settings/profile`,
`/portfolio/edit`, `/portfolio/transactions` 순회. 데스크톱/모바일(개발자도구 반응형) 폭에서
차트 노출 차이 확인. 보유 1줄 편집 저장 → `/portfolio` 갱신 확인.

- [ ] **Step 7: 회귀 — 기존 스위트**

Run: `pytest -q`
Expected: 기존 테스트 + 신규 웹 테스트 모두 PASS (기존 코드 무수정이므로 회귀 없음).

- [ ] **Step 8: 커밋**

```bash
git add croesus/web/static README.md
git commit -m "✨ feat: adaptive CSS, vendored echarts/htmx, chart bootstrap + docs"
```

---

## Self-Review

**Spec coverage:**
- §2 핵심 결정(읽기+설정쓰기, 적응형, 벤더링) → Task 1·2·12. ✓
- §4 동시성(read/write 연결, 503 폴백, TTL 캐시) → Task 2·3·7. ✓
- §5 재사용 함수(macro/screening/portfolio/opportunity 읽기) → Task 4–8. ✓
- §6 적응형 레이아웃 → Task 12 CSS 브레이크포인트. ✓
- §7 페이지·차트(도넛/라인/게이지/레인지/산점도/추천행동카드) → Task 4–8·12. ✓
- §7 Phase E risk-gate(카드 verdict 배지·gate_summary·상태 필터·상세 notes) → Task 3 뷰모델 + Task 7. ✓
- §8 Tailscale → Task 1 `__main__` URL 출력 + Task 12 README. ✓
- §9 의존성 → Task 1. ✓
- §10 테스트 → 각 Task TDD + Task 12 회귀. ✓
- §12 설정/편집(프로필/보유/거래) → Task 9·10·11. ✓
- §13 미해결(포트폴리오 id, run_portfolio_snapshot 연결주입, 캐시 무효화) → resolve_portfolio_id, Task 10(snapshot+invalidate). ✓

**구현 시 반드시 대조할 시그니처(실파일 확인 — 플랜에 메모됨):**
- `ProfileValidationResult`의 에러 컬렉션 속성명(Task 9 메모).
- `ProfileRepository.get_policy_targets` 존재 여부(Task 9 메모, 없으면 폴백).
- `PortfolioTransaction` 생성자 필드명(Task 11 메모).
- `TransactionRepository.record_transaction` 위치/시그니처(`transaction_repository.py:36`).
- `PortfolioRepository.get_snapshot` 반환 dict 키(`total_market_value`,`unrealized_pnl`).
- **Phase E 확인됨(PR #49)**: `RiskGateVerdict(status, reason_codes, notes)` (`opportunities/risk_gate.py:34`);
  `OpportunityCard.risk_gate` 신규 필드(`review.py:49`); `OpportunityReviewResult.gate_summary` /
  `recommendation_only`; `run_opportunity_review`에 `portfolio_id/profile_id/apply_risk_gate(기본 True)/min_liquidity_usd` 추가. 신규 테이블 없음.

**Placeholder scan:** 모든 코드 step에 실제 코드 포함. "TBD/적절히 처리" 없음. ✓
**Type consistency:** 뷰모델은 Task 3에서 1회 정의 후 Task 4–8에서 동일 필드 사용. `build_*` 함수명·`opportunity_cache` 일관. ✓
