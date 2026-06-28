# Croesus 웹 대시보드 — 설계 (v1)

- **상태**: 설계 승인 대기 → 구현 플랜 작성 예정
- **작성일**: 2026-06-23
- **범위**: 인터랙티브·적응형 웹앱. **읽기**(macro · screening · portfolio · opportunity 시각화) +
  **설정 쓰기**(프로필 · 포트폴리오 보유 · 거래 원장 편집)

---

## 1. 배경 / 동기

Croesus 파이프라인은 도메인별 결과를 `reports/<domain>/<YYYY-MM-DD>/`에 마크다운+CSV로,
그리고 `storage/croesus.duckdb`의 테이블에 적재한다. 현재는 이 산출물을 보려면 파일을 직접
열거나 CLI를 써야 한다. 사용자는 **폰·태블릿·컴퓨터에서 결과를 보기 좋게 확인**하고 싶어 하며,
Tailscale로 어디서든 접근하기를 원한다.

목표: (1) 파이프라인이 이미 계산해 둔 결과를 **시각화해 전달**하고, (2) 사용자가 지금은 CLI·CSV·YAML로만
가능한 **프로필·포트폴리오·거래 설정을 웹에서 직접 편집**하게 한다.

쓰기 범위는 **설정(configuration)**에 한정한다: 프로필(위험 설정 + 슬리브 타깃), 포트폴리오 보유,
거래 원장. 파이프라인 계산 자체나 **추천 행동의 승인·실행은 하지 않는다**(표시만, Phase E 보류).
즉 "사용자가 자기 상황을 입력/갱신 → 시스템이 분석·추천 → 표시"의 입력단과 출력단을 웹이 담당하고,
중간 계산과 의사결정 실행은 기존 파이프라인/사람의 몫으로 남긴다.

## 2. 핵심 결정 (확정)

| 결정 | 선택 | 근거 |
|---|---|---|
| 앱의 목적 | **읽기 대시보드 + 설정 편집** | 읽기 + 프로필·포트폴리오·거래 설정 쓰기. 계산/실행은 비범위 |
| 보유 편집 방식 | **인라인 표 편집기** | 브라우저에서 행 추가/수정/삭제, 심볼 자동완성, 즉시 검증 |
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
  db.py              # get_read_connection() + get_write_connection() 컨텍스트매니저, 락 우아 처리
  services.py        # 기존 repo를 감싼 얇은 뷰모델 빌더 + asset_id→(symbol,name) 매핑 + 날짜/포트폴리오 해석
  forms.py           # 폼 입력 → 도메인 모델 변환 + 기존 검증 함수 호출, 에러 메시지 수집
  cache.py           # 단순 TTL 인메모리 캐시(opportunity 재계산용)
  routes/
    home.py          # GET /
    macro.py         # GET /macro
    opportunity.py   # GET /opportunities, GET /opportunities/{asset_id}
    portfolio.py     # GET /portfolio, GET /portfolio/edit, POST /portfolio/holdings,
                     #   GET /portfolio/transactions, POST /portfolio/transactions
    screening.py     # GET /screening
    settings.py      # GET /settings/profile, POST /settings/profile
  templates/
    base.html        # 모바일 하단 네비(+설정) + 반응형 그리드 셸, 다크/라이트 자동
    home.html, macro.html, opportunities.html, opportunity_detail.html, portfolio.html, screening.html
    portfolio_edit.html, transactions.html, settings_profile.html
    partials/        # HTMX 부분 렌더(필터·정렬·검증 에러·행 추가 조각)
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

### 쓰기 연결 (설정 저장용)

설정 편집은 read-write 연결이 필요하다. `get_write_connection()`는 **저장 POST 처리 동안에만**
`duckdb.connect(path, read_only=False)`로 열고 즉시 닫는다(읽기와 동일하게 단명).

- 같은 read-write 연결로 쓰기와 후처리 재계산을 모두 수행한다(read-write 연결은 읽기도 가능).
- 싱크가 락을 쥔 순간 충돌 시 `DataUpdatingError` → **409/503 + "동기화 중이라 저장 실패, 재시도"**.
  저장은 사용자 1인이 가끔 하는 일이라 충돌 확률이 낮고 재시도로 충분하다.
- 한 요청 안에서 read_only와 read-write 연결을 **동시에 보유하지 않는다**(저장 요청은 쓰기 연결만 사용).

## 5. 재사용 함수 (전부 기존 — 신규 SQL 최소)

| 도메인 | 호출 | 반환 | 테이블 |
|---|---|---|---|
| opportunity | `run_opportunity_review(conn, methodology_key="moat_adjusted_intrinsic_value", as_of_date=d, portfolio_id=pid, profile_id="default")` | `OpportunityReviewResult` (`.cards: list[OpportunityCard]`, `.gate_summary: dict[str,int]`, `.recommendation_only`) | intrinsic_value_bands, valuation_snapshots, thesis_grades, assets, portfolio_holdings, portfolio_exposures, factor_values |
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

### `/opportunities` (Phase E risk-gate 반영)
- 상단 **게이트 요약**: `gate_summary` = N pass / N warn / N block (색 배지). 게이트 상태로 필터(HTMX).
- 정렬 가능한 카드: 심볼·현재가·base 업사이드·thesis 확신도 + **risk-gate verdict 배지**
  (`status` pass=초록/warn=주황/block=빨강) + `reason_codes`(예: SECTOR_OVER_MAX, LIQUIDITY_BELOW_MINIMUM).
- 시각화: bear/base/bull **레인지 바** · thesis 등급(moat/tech/sector/disruption) **색칩 히트** ·
  (데스크톱) 업사이드 vs 확신도 **버블 산점도**(점 색 = 게이트 상태).
- 상세 `/opportunities/{asset_id}`: 밴드 차트 + 전 evidence 텍스트 + bear case + **게이트 notes 전체**(사람이 읽는 사유).
- **추천 전용**: 게이트는 재랭킹·매매 제안·쓰기를 하지 않음(Phase E 원칙). 대시보드도 표시만.

> Phase E는 `run_opportunity_review`에서 `apply_risk_gate=True`(기본)로 카드에 `risk_gate: RiskGateVerdict`
> (`status`, `reason_codes`, `notes`)를 붙이고 결과에 `gate_summary`를 담는다. 신규 테이블 없음(리뷰 시 재계산).
> 대시보드는 `portfolio_id`(resolve) + `profile_id="default"`로 호출해 게이트를 함께 받아 표시한다.

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
- `python-multipart>=0.0.9` (FastAPI 폼 데이터 처리에 필요 — 설정/편집 POST)

htmx·echarts는 `static/`에 벤더링(파이썬 의존 아님). `[project.scripts]`는 추가하지 않음(`python -m` 관례 유지).

## 10. 테스트

- FastAPI `TestClient` + 시드된 임시 DuckDB(기존 테스트 시딩 관례 따름).
- 각 라우트가 200을 반환하고 기대 데이터(심볼·수치·차트 컨테이너)를 포함하는지 검증.
- `get_read_connection`의 락 폴백: 외부에서 RW 연결을 쥔 상태를 시뮬레이션 → 503 "동기화 중" 확인.
- 홈 집계(추천 행동 카드·경보) 스모크 테스트.
- 데이터 없는 도메인의 빈 상태(친절한 "아직 데이터 없음") 렌더 확인.
- **쓰기 경로**: 프로필 저장 POST → DB 반영 + 잘못된 입력(가중치 합≠1) 거부·에러 표시 검증.
  보유 편집 POST → `replace_holdings` 반영 + 스냅샷 재계산으로 익스포저 갱신 확인.
  거래 추가 POST → 원장 append + 검증 실패(수량≤0 등) 거부.
- 쓰기 연결 락 폴백: 외부 RW 점유 시 저장이 409/503로 우아하게 실패하는지.

## 11. v1 비범위

- **추천 행동의 승인·실행** (Phase E) — 표시만, 버튼 없음
- 파이프라인 계산 트리거(스크리닝/매크로/밸류에이션 재실행 등) — 보유 편집 후 스냅샷 재계산은 예외(13장)
- 앱 레벨 인증 (Tailnet이 경계)
- backtest · forward_test · performance 페이지 (v2)
- 웹소켓 실시간 푸시 / 자동 새로고침
- 멀티 포트폴리오 전환 (활성 포트폴리오 1개 = `"default"` 가정)

## 12. 설정/편집 (쓰기 경로)

현재 프로필·포트폴리오·거래는 CLI·CSV·YAML로만 설정 가능하다. 이를 웹 폼으로 옮기되
**기존 쓰기 함수와 검증을 그대로 재사용**한다(신규 비즈니스 로직 없음, 폼↔모델 변환 + 표시만 추가).

### 12.1 프로필 편집 — `GET/POST /settings/profile`
- 폼 필드 = `InvestorProfile`(`profiles/models.py:49`): 기대수익·최대드로다운·투자기간·월납입·유동성버퍼,
  한도(단일/섹터/산업/테마/국가/통화 비중)·월회전·리밸런싱 밴드·trade_mode·허용/비허용 자산유형,
  그리고 슬리브 타깃 표(`PolicyTarget`: sleeve_name·target/min/max_weight + 자산 매핑 metadata).
- 저장: `forms.py`가 폼 → `InvestorProfile` + `list[PolicyTarget]` 변환 → `validate_profile()` +
  `validate_policy_targets()`(`profiles/validation.py`) 호출 → 통과 시 `ProfileRepository.save_profile()`(원자적).
- 검증 에러(가중치 합=1, 드로다운<0, min≤target≤max 등)는 **에러로 저장 차단**, 경고는 비차단 표시(HTMX 인라인).

### 12.2 포트폴리오 보유 편집 — `GET /portfolio/edit`, `POST /portfolio/holdings`
- **인라인 표 편집기**: 행 = 종목(symbol 자동완성 → `assets`)·수량·평균단가·통화(또는 현금행은 평가액).
  행 추가/수정/삭제는 HTMX 부분 렌더.
- 저장: 폼 행 → `list[Holding]` 변환, `AssetResolver`로 symbol→asset_id 해석(`import_holdings.py`의 행 검증
  규칙 재사용: 미해결 심볼·현금행 평가액 필수 등) → `PortfolioRepository.replace_holdings()`(원자적 교체).
- **저장 직후 스냅샷 재계산**: 같은 쓰기 연결로 `run_portfolio_snapshot`을 호출해 평가액·익스포저·드리프트를
  갱신 → 대시보드 즉시 반영. (파이프라인 일반 잡 러너가 아니라 방금 한 편집의 후처리.)
- **안전장치**: 교체 전 직전 보유를 백업 행/CSV로 보존(기존 `holdings_backup_*.csv` 관례), 전체 삭제 등
  파괴적 변경은 확인 단계.

### 12.3 거래 원장 — `GET /portfolio/transactions`, `POST /portfolio/transactions`
- 폼 = `PortfolioTransaction`(`portfolio/transactions.py:58`): 유형(buy/sell/deposit/withdrawal/dividend/fee/
  manual_adjustment)·종목·수량·가격·금액·통화·수수료·일자.
- 저장: `validate_transaction()` → `record_manual_transaction()`/`TransactionRepository.record_transaction()`(append-only).
- 원장 목록 표시 + 합계. 보유를 원장에서 파생하려면 `derive_holdings_from_transactions()` 재사용.
- **주의(기존 동작)**: CSV 보유와 거래 원장이 둘 다 있으면 스냅샷 시 수량 차이가 경고로 표시됨
  (`portfolio_snapshot.py:227`). 편집기와 원장은 같은 `portfolio_holdings`로 수렴하며 이 정합성 경고를 그대로 노출.

### 12.4 네비게이션 / UX
- base.html에 **설정(⚙)** 진입점 추가. 모바일에서도 폼은 1열·큰 터치 타깃.
- 저장 성공/실패는 토스트 + 인라인 검증 메시지. 적응형: 데스크톱은 폼+미리보기(예: 슬리브 도넛) 나란히.

## 13. 미해결 / 구현 시 확인

- 기본 포트폴리오는 `"default"` 단일(`is_active` 컬럼 없음) — services가 이를 기본값으로 사용.
- 벤더링할 htmx/echarts 버전 고정.
- opportunity TTL 캐시 키 = `(methodology_key, as_of_date)`.
- `run_portfolio_snapshot`을 웹의 쓰기 연결로 재호출하는 방식 — 잡 함수가 외부 connection을 받는지,
  아니면 내부에서 `get_connection`을 여는지 확인해 후자면 얇은 래퍼로 connection 주입(구현 시).
- 보유 편집 백업 형식(별도 CSV vs metadata 보존) 및 보존 위치 결정.
- 편집 후 캐시 무효화: 보유/프로필 저장 시 관련 TTL 캐시(opportunity·집계) 즉시 무효화.
