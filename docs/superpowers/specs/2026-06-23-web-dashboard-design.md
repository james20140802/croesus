# Croesus 웹 대시보드 — 설계 (v1)

- **상태**: 설계 승인 대기 → 구현 플랜 작성 예정
- **작성일**: 2026-06-23
- **범위**: 읽기 전용, 인터랙티브, 적응형 웹 대시보드 (v1: macro · screening · portfolio · opportunity)

---

## 1. 배경 / 동기

Croesus 파이프라인은 도메인별 결과를 `reports/<domain>/<YYYY-MM-DD>/`에 마크다운+CSV로,
그리고 `storage/croesus.duckdb`의 테이블에 적재한다. 현재는 이 산출물을 보려면 파일을 직접
열거나 CLI를 써야 한다. 사용자는 **폰·태블릿·컴퓨터에서 결과를 보기 좋게 확인**하고 싶어 하며,
Tailscale로 어디서든 접근하기를 원한다.

목표: 파이프라인이 이미 계산해 둔 결과를 **시각화해 전달하는 읽기 전용 웹앱**.
계산/쓰기는 일절 하지 않는다(추천 행동도 "표시만", 승인·실행은 v1 비범위 = Phase E 보류).

## 2. 핵심 결정 (확정)

| 결정 | 선택 | 근거 |
|---|---|---|
| 앱의 목적 | **읽기 전용 대시보드** | 쓰기 경로 없음 → 설계 단순, 파이프라인과 단방향 |
| 경험 | **인터랙티브 데이터 앱** | DuckDB 라이브 조회 + 차트/정렬/드릴다운 |
| 스택 | **FastAPI + HTMX + Jinja2 + ECharts** | 순수 Python, npm 빌드 없음, 모바일 친화, Python-우선 철학과 일치 |
| 레이아웃 | **적응형 (mobile-first, 단일 코드베이스)** | 화면이 커질수록 정보 밀도·차트 증가 |
| v1 도메인 | **macro · screening · portfolio · opportunity** | backtest/forward/performance는 v2 |
| 정적 자산 | **htmx·echarts 벤더링** (CDN 아님) | 오프라인·무외부의존, 자기완결 |
| 인증 | **없음 (Tailnet이 경계)** | 1인 자체호스팅 |

## 3. 아키텍처

기존 잡/계산 코드를 **일절 수정하지 않고** 기존 리포지토리만 호출하는 단방향 의존:

```
브라우저(폰/태블릿/PC) ──HTTP──> FastAPI(croesus/web) ──> 기존 Repository ──read_only──> DuckDB
```

### 모듈 구조

```
croesus/web/
  __init__.py
  __main__.py        # argparse(--host/--port/--db-path) → uvicorn.run; tailscale URL 출력. `python -m croesus.web`
  app.py             # FastAPI 앱 팩토리: 라우트 등록, Jinja2 + static 마운트
  db.py              # get_read_connection() 컨텍스트매니저, 락 우아 처리
  services.py        # 기존 repo를 감싼 얇은 뷰모델 빌더 + asset_id→(symbol,name) 매핑 + 날짜/포트폴리오 해석
  cache.py           # 단순 TTL 인메모리 캐시(opportunity 재계산용)
  routes/
    home.py          # GET /
    macro.py         # GET /macro
    opportunity.py   # GET /opportunities, GET /opportunities/{asset_id}
    portfolio.py     # GET /portfolio
    screening.py     # GET /screening
  templates/
    base.html        # 모바일 하단 네비 + 반응형 그리드 셸, 다크/라이트 자동
    home.html, macro.html, opportunities.html, opportunity_detail.html, portfolio.html, screening.html
    partials/        # HTMX 부분 렌더(필터·정렬 결과 조각)
  static/
    css/app.css      # CSS 그리드 브레이크포인트, 의미 컬러, 애니메이션
    js/echarts.min.js, js/htmx.min.js, js/charts.js   # 벤더링 + 차트 초기화
```

`python -m croesus.jobs.<job>` 관례를 그대로 따라 `python -m croesus.web`로 기동.

## 4. 데이터 접근 & 동시성 (핵심 위험)

`croesus/db/connection.py:24`의 `get_connection()`은 **read-write로만** 열고, 코드베이스 전체에
`read_only` 사용이 0건이다. DuckDB 파일 모드는 **단일 writer**만 허용 → 데일리 싱크가 파일을
쥔 동안 다른 프로세스의 연결은 실패한다.

**해결**: `croesus/web/db.py`에 전용 컨텍스트매니저를 둔다.

```python
@contextmanager
def get_read_connection(db_path=None):
    path = resolve_db_path(db_path)          # 기존 resolve_db_path 재사용
    try:
        conn = duckdb.connect(str(path), read_only=True)
    except (duckdb.IOException, duckdb.Error) as exc:
        raise DataUpdatingError() from exc    # 라우트에서 503 "동기화 중"으로 변환
    try:
        yield conn
    finally:
        conn.close()
```

- **요청당 개방·즉시 종료** → 웹이 영구 연결로 writer를 막지 않는다(파이프라인 보호).
- 싱크가 RW 락을 쥔 짧은 순간엔 read_only 연결도 실패 → `DataUpdatingError`를 잡아
  **503 + "데이터 동기화 중, 잠시 후 새로고침"** 페이지로 우아하게 처리.
- **opportunity 리뷰**는 매 호출 시 자산을 재계산(가장 무거운 읽기) → `cache.py`의 **60초 TTL 캐시**로 감싼다.

## 5. 재사용 함수 (전부 기존 — 신규 SQL 최소)

| 도메인 | 호출 | 반환 | 테이블 |
|---|---|---|---|
| opportunity | `run_opportunity_review(conn, methodology_key="moat_adjusted_intrinsic_value", as_of_date=d)` | `OpportunityReviewResult.cards: list[OpportunityCard]` | intrinsic_value_bands, valuation_snapshots, thesis_grades, assets |
| portfolio 보유 | `PortfolioRepository(conn).get_holdings(pid, d)` | `list[Holding]` | portfolio_holdings |
| portfolio 익스포저 | `…get_exposures(pid, d)` | `list[Exposure]` | portfolio_exposures |
| portfolio 드리프트 | `…get_drifts(pid, d)` | `list[PolicyDrift]` | policy_drifts |
| portfolio 스냅샷 | `…get_snapshot(pid, d)` | `dict` | portfolio_snapshots |
| **추천 행동** | `…load_latest_rebalance_run(pid)` | `dict` + `"actions": list[ProposedAction]` | rebalance_runs, proposed_actions |
| screening | `ScreeningRepository(conn).list_results(run_id)` | `list[ScreeningCandidate]` | screening_results |
| macro | `load_latest_macro_state(conn)` (`croesus.macro._loader`) | `MacroState` | macro_scores |
| 최신 리포트 경로 | `latest_reports(conn)` (`croesus.reports.registry`) | `list[RegisteredReport]` | reports |

**신규 SQL 2건만 추가**:
1. screening 최신 run_id — 기존 패턴 재사용(`rebalance_check.py:167`):
   `SELECT run_id FROM screening_results GROUP BY run_id ORDER BY run_id DESC LIMIT 1`
2. macro 추이 차트 — `SELECT date, regime, positioning, amplifier_score, confirmation_score FROM macro_scores ORDER BY date DESC LIMIT N`

**날짜/포트폴리오 해석**(services.py): `portfolio_id`는 `portfolios` 테이블의 활성/단일
포트폴리오로 자동 해석, `as_of_date`는 도메인별 최신 가용 날짜로 자동 해석.

**심볼 보강**: `Holding/Exposure/ScreeningCandidate`는 `asset_id`만 가지므로 `assets`에서
`asset_id → (symbol, name)` 매핑을 요청당 1회 조회해 표시에 사용(`OpportunityCard`는 이미 symbol/name 보유).

## 6. 적응형 레이아웃

단일 코드베이스 + CSS 브레이크포인트(mobile-first). 같은 Jinja 템플릿, ECharts는 컨테이너
반응형 자동 리사이즈.

| 브레이크포인트 | 레이아웃 | 차트 |
|---|---|---|
| 모바일 (~640px) | 1열, 핵심 숫자 + 히어로 차트 1개, 상세 접기 | 단순화/일부 생략 |
| 태블릿 (641–1024px) | 2열, 차트+표 나란히 | 주요 차트 |
| 데스크톱 (1024px+) | 멀티컬럼 대시보드, 호버 인터랙션 풀 | 전 차트 + 보조 시각화 |

데스크톱 전용 보조 차트는 `data-min-width` 게이트로 그 폭 이상에서만 렌더(모바일 연산·대역폭 절약).

## 7. 페이지 & 시각화

### 홈 `/` — "오늘 한눈에"
- **최상단: "오늘의 추천 행동" 전용 카드** — 핵심 1~3건 요약(action_type + 사람이 읽는 사유 + 건수),
  `/portfolio` 제안 액션 섹션으로 연결. (데이터: `load_latest_rebalance_run`)
- macro 레짐/포지셔닝 배지 · 상위 업사이드 기회 수 · 드리프트/위반 경보 · 스크리닝 숏리스트 수
- 도메인별 신선도(as-of) 배지
- 시각화: macro 2×2 사분면(현재 위치) · 포트폴리오 슬리브 **도넛** · (데스크톱) 스파크라인 띠

### `/macro`
- 현재 레짐(성장×인플레 2×2), 포지셔닝, 경고·기회 리스트, 4-method 레짐 분해
- 시각화: amplifier/confirmation **게이지** 2개 · amplifier·confirmation **라인차트**(히스토리) ·
  성장×인플레 **2×2 사분면** · 4-method **레이더/막대**

### `/opportunities`
- 정렬 가능한 카드: 심볼·현재가·base 업사이드·thesis 확신도
- 시각화: bear/base/bull **레인지 바** · thesis 등급(moat/tech/sector/disruption) **색칩 히트** ·
  (데스크톱) 업사이드 vs 확신도 **버블 산점도**
- 상세 `/opportunities/{asset_id}`: 밴드 차트 + 전 evidence 텍스트 + bear case

### `/portfolio`
- 총평가액·손익, 보유 표
- **제안 액션 섹션**(추천 행동 상세): action_type·reason_codes·사람이 읽는 사유·estimated_trade_value
  (읽기 전용 — 승인/실행 버튼 없음)
- 시각화: 구성 **도넛**(슬리브/섹터) · 익스포저 **한도선 막대**(위반 빨강) ·
  정책 드리프트 **발산형 막대**(타깃 중심 ±)

### `/screening`
- 최신 run 숏리스트 표: 랭크·심볼·점수·decision_bucket·사유, bucket 필터(HTMX)
- 시각화: 점수 **수평 막대** 랭킹 · (데스크톱) 후보별 팩터 **레이더**(모멘텀/유동성/추세/밸류)

### "즐거운" 디테일
의미 기반 컬러(레짐·등급·위반/정상) · 부드러운 진입 애니메이션 · 숫자 카운트업 ·
ECharts 호버 툴팁/하이라이트 · 시스템 `prefers-color-scheme` 다크/라이트 자동.

## 8. Tailscale

- tailnet 인터페이스(또는 `0.0.0.0`)에 바인딩. **Tailnet 자체가 인증 경계** → v1 앱 인증 없음.
- `__main__`이 기동 시 접속 URL 출력: `http://<host>.<tailnet>.ts.net:<port>`
  (`tailscale ip -4`/hostname으로 감지, 실패 시 일반 host:port 출력).
- HTTPS가 필요하면 `tailscale serve` 사용법을 README에 문서화(선택, v1 필수 아님).

## 9. 의존성

`pyproject.toml` `[project].dependencies`에 추가:
- `fastapi>=0.110`
- `uvicorn[standard]>=0.29`
- `jinja2>=3.1`

htmx·echarts는 `static/`에 벤더링(파이썬 의존 아님). `[project.scripts]`는 추가하지 않음(`python -m` 관례 유지).

## 10. 테스트

- FastAPI `TestClient` + 시드된 임시 DuckDB(기존 테스트 시딩 관례 따름).
- 각 라우트가 200을 반환하고 기대 데이터(심볼·수치·차트 컨테이너)를 포함하는지 검증.
- `get_read_connection`의 락 폴백: 외부에서 RW 연결을 쥔 상태를 시뮬레이션 → 503 "동기화 중" 확인.
- 홈 집계(추천 행동 카드·경보) 스모크 테스트.
- 데이터 없는 도메인의 빈 상태(친절한 "아직 데이터 없음") 렌더 확인.

## 11. v1 비범위

- 쓰기/액션 승인·실행 (Phase E)
- 앱 레벨 인증
- backtest · forward_test · performance 페이지 (v2)
- 웹소켓 실시간 푸시 / 자동 새로고침
- 멀티 포트폴리오 전환 (활성 포트폴리오 1개 가정)

## 12. 미해결 / 구현 시 확인

- `portfolios` 테이블에서 "활성/기본" 포트폴리오를 고르는 규칙(컬럼 존재 여부) — 구현 시 스키마 확인.
- 벤더링할 htmx/echarts 버전 고정.
- opportunity TTL 캐시 키 = `(methodology_key, as_of_date)`.
