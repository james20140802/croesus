# Macro → Screening Wiring — Design Spec

_2026-05-30_

## Summary

Sprint 002는 `MacroState`를 산출하고 `macro_scores` 테이블에 저장하며
`get_screening_params(state)`로 스크리닝 파라미터까지 변환한다. 그러나 이 출력을
실제 일일 파이프라인(`daily_run.py`: 가격 수집 → 팩터 계산)이 **전혀 소비하지 않는다**.
또한 `macro_scores` 테이블을 **다시 읽어오는 코드가 어디에도 없다**.

이 작업은 그 끊어진 고리를 잇는다 — **최소 연결(wiring only)** 범위로,
실제 종목 랭킹 엔진은 만들지 않는다 (Sprint 003 영역).

목표: "`MacroState`가 실제 일일 파이프라인에 소비된다"를 코드와 테스트로 증명한다.

---

## Scope

### In scope
- `macro_scores` 테이블에서 최신 `MacroState`를 복원하는 reader 함수.
- `daily_run.py`가 reader → `get_screening_params()`를 호출해 조정된 파라미터를 얻고, 로그/결과로 출력.
- 매크로 데이터가 없을 때(테이블 없음/빈 테이블)의 중립 fallback.

### Out of scope (Sprint 003)
- `factor_values`를 읽어 종목을 실제로 랭킹하는 screening 엔진(`croesus/screening/`).
- `screening_results` 테이블에 랭킹 결과 기록.
- `candidate_count`/필터를 실제 종목 집합에 적용.

`daily_run`은 조정된 파라미터를 **로그로 출력**하는 데서 끝난다. 종목 랭킹은 하지 않는다.

---

## Architecture (단방향 의존 유지)

```
daily_macro_run  →  macro_scores 테이블        [기존 구현]
                          │
                          ▼  load_latest_macro_state()   [신규 reader]
daily_run  ────────────────────────────────────────────────┐
  seed → ingest prices → compute factors                    │
                          │                                  ▼
                          └──→ MacroState 있음 ─→ get_screening_params(state)
                                       │ 없음                 │
                                       ▼                       ▼
                              중립 fallback params ──→ DailyRunResult.screening_params
                                                              │
                                                              ▼
                                                    main()에서 로그 출력
```

- Macro 모듈은 여전히 스크리닝을 모른다. `daily_run`이 reader로 결과를 **당겨온다(pull)**.
- DB 경유라 `daily_run`은 FRED/yfinance 네트워크 수집에 의존하지 않는다 — 가격/팩터 파이프라인과 매크로 수집이 분리된 채 유지된다.

---

## Components

### 1. `croesus/macro/_loader.py` — `load_latest_macro_state()` 추가

`store_macro_state`의 짝. 같은 파일에 두어 저장/복원 로직의 응집도를 유지한다.

```python
def load_latest_macro_state(db_path=None) -> MacroState | None:
    """macro_scores에서 가장 최근 date의 row를 MacroState로 복원. 없으면 None."""
```

- `SELECT ... FROM macro_scores ORDER BY date DESC LIMIT 1`.
- JSON 컬럼(`warnings`, `opportunities`, `raw_indicators`, `regime_methods`)은 `json.loads`로 역직렬화.
- `date`는 DuckDB DATE → `datetime.date`로 복원.
- 테이블이 없거나(마이그레이션 전) row가 0개면 `None` 반환.

### 2. `croesus/jobs/daily_run.py` — 배선

- `DailyRunResult`에 `screening_params: dict` 필드 추가.
- `run_daily_pipeline`:
  - 가격·팩터 계산 후 `load_latest_macro_state()` 호출.
  - `state`가 있으면 `get_screening_params(state)`, 없으면 `_neutral_screening_params()` (config의 base_weights + base_candidate_count, 필터 없음).
  - 매크로 데이터 부재 시 `log(...)`로 "daily_macro_run 미실행 — 중립 파라미터 사용" 경고.
- `main()`: regime / positioning / factor_weights / candidate_count를 출력.

중립 fallback은 `screening_adapter`에서 config를 읽어 구성한다 (하드코딩 금지, CLAUDE.md 원칙).

---

## Error Handling

| 상황 | 동작 |
|------|------|
| `macro_scores` 테이블 없음 (마이그레이션 전) | reader가 `None` 반환 → 중립 fallback |
| 테이블 있으나 row 0개 (daily_macro_run 미실행) | `None` → 중립 fallback + 경고 로그 |
| 정상 | 최신 MacroState 복원 → 조정 파라미터 |

reader는 예외를 던지지 않고 `None`으로 흡수한다 (파이프라인이 매크로 부재로 깨지지 않도록).

---

## Testing

`store_macro_state(state, db_path=)`와 `get_connection(db_path=)`가 경로를 받으므로 임시 DB 파일로 테스트한다.

1. **reader 라운드트립**: `migrate(tmp)` → `store_macro_state(state, tmp)` → `load_latest_macro_state(tmp)` → 모든 필드(특히 JSON 4종, date) 일치.
2. **최신 row 선택**: 서로 다른 두 날짜를 저장 → reader가 더 최근 날짜를 반환.
3. **빈 테이블**: `migrate(tmp)`만 → `load_latest_macro_state(tmp)` == `None`.
4. **`run_daily_pipeline` 매크로 있음**: macro_scores에 row 저장 후 실행 → `result.screening_params`의 `regime`이 저장한 값과 일치.
5. **`run_daily_pipeline` 매크로 없음**: macro row 없이 실행 → `screening_params`가 중립 base_weights로 채워짐.

기존 56개 테스트는 그대로 통과해야 한다.

---

## Design Principle Alignment

- ✅ **단방향 의존**: macro→screening 방향 유지. daily_run이 pull.
- ✅ **결정론적**: LLM 미사용, 전부 수치/규칙.
- ✅ **config 분리**: 중립 fallback도 `config.yaml`에서 읽음.
- ✅ **모듈 분리**: 데이터 수집 / 팩터 / 매크로 / 배선이 섞이지 않음.
- ✅ **graceful skip**: 매크로 부재 시 파이프라인이 깨지지 않음.
