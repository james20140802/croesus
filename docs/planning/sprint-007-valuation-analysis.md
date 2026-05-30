# Sprint 007: Valuation Analysis Layer

## Goal

개별 종목의 펀더멘털 밸류에이션을 Factor Engine에 추가한다.

```text
Fundamentals Ingestion (yfinance)
  -> fundamentals 테이블 저장
  -> Relative Valuation (P/E, P/B, EV/EBITDA, FCF yield, 섹터 백분위)
  -> Absolute Valuation (2-stage DCF with CAPM WACC)
  -> factor_values (스크리닝 통합)
  -> valuation_snapshots (DCF 상세 기록)
```

Sprint 001~006이 완료되어 profile-first portfolio workflow와 Level 1 rebalancing proposal engine이 준비된 이후를 전제로 한다.

---

## Scope

### 1. Schema 업데이트

`fundamentals`와 `valuation_snapshots` 테이블을 `schema.sql`에 추가하고 `migrate.py`에서 반영한다.

```sql
CREATE TABLE IF NOT EXISTS fundamentals (
  asset_id     TEXT NOT NULL,
  period_end   DATE NOT NULL,
  period_type  TEXT NOT NULL,   -- 'annual' | 'quarterly'
  metric_name  TEXT NOT NULL,
  value        DOUBLE,
  source       TEXT,
  PRIMARY KEY (asset_id, period_end, period_type, metric_name)
);

CREATE TABLE IF NOT EXISTS valuation_snapshots (
  asset_id                  TEXT NOT NULL,
  date                      DATE NOT NULL,
  intrinsic_value_per_share DOUBLE,
  current_price             DOUBLE,
  upside_pct                DOUBLE,
  wacc                      DOUBLE,
  fcf_growth_rate           DOUBLE,
  terminal_growth_rate      DOUBLE,
  assumptions_json          TEXT,
  PRIMARY KEY (asset_id, date)
);
```

### 2. FundamentalsProvider 인터페이스

```text
croesus/data_sources/fundamentals/
  base.py                   -- FundamentalsProvider ABC
  yfinance_fundamentals.py  -- yfinance 구현체
```

`FundamentalsProvider.get_financials(symbol)` 반환값:

```python
{
    "income_annual":    DataFrame,  # 연간 손익계산서
    "income_quarterly": DataFrame,  # 분기 손익계산서
    "balance_annual":   DataFrame,  # 연간 대차대조표
    "cashflow_annual":  DataFrame,  # 연간 현금흐름표
}
```

각 DataFrame은 결산일을 컬럼, 지표명을 인덱스로 사용.

### 3. Fundamentals Ingestion

```text
croesus/fundamentals/
  ingest_fundamentals.py  -- FundamentalsProvider 호출 → 정규화 → fundamentals 저장
  repository.py           -- get_annual_fcf(), get_latest_metric() 등 조회 헬퍼
```

**수집 지표:**
`revenue`, `operating_income`, `net_income`, `eps`, `free_cash_flow`,
`total_debt`, `total_equity`, `cash_and_equivalents`, `shares_outstanding`,
`ebitda`, `capex`, `book_value_per_share`

**정규화 규칙:**
- 결산일을 `period_end`로 변환.
- 모든 통화 단위를 원본 그대로 저장 (USD 기준).
- 개별 종목 실패 시 해당 종목 건너뛰고 계속 진행.
- 수집 실패는 명확히 로깅.

### 4. Valuation Factor Computation

```text
croesus/factors/equity/
  valuation.py  -- compute_valuation_factors() 진입점
```

**계산 흐름:**

```
compute_valuation_factors(asset_id, date, conn)
  │
  ├── compute_multiples()
  │     pe_ratio      = price / eps (최근 연간)
  │     pb_ratio      = price / book_value_per_share
  │     ev_to_ebitda  = (market_cap + total_debt - cash) / ebitda
  │     fcf_yield     = free_cash_flow / market_cap
  │
  ├── compute_sector_percentiles()
  │     assets.sector로 동일 섹터 종목 조회
  │     각 멀티플의 섹터 내 백분위 계산 (0 = 가장 저평가)
  │     → pe_vs_sector_pct, pb_vs_sector_pct, ev_ebitda_vs_sector_pct
  │
  ├── compute_dcf(overrides=None)
  │     compute_wacc(): CAPM = Rf + β × 5.5%
  │       Rf = macro_scores의 10Y 국채 수익률 (없으면 4.5%)
  │       β  = 2년 일별 수익률 vs SPY 회귀 (없으면 섹터 중앙값 → 1.0)
  │     compute_fcf_growth(): 최근 5년 FCF CAGR, [-5%, +30%] 클리핑
  │     2-stage DCF: 5년 명시 예측 + Gordon Growth 터미널 가치
  │     → DcfResult(intrinsic_value_per_share, wacc, growth_rate, …)
  │
  ├── → factor_values 저장 (8개 스칼라 팩터)
  └── → valuation_snapshots 저장 (DCF 상세)
```

**데이터 부족 처리:**

| 상황 | 처리 |
|------|------|
| FCF 유효 연도 3년 미만 | DCF 스킵, `price_to_intrinsic = NULL` |
| FCF 전 기간 음수 | DCF 스킵, 로그 기록 |
| Beta 계산 데이터 부족 | 섹터 중앙값 → 1.0 폴백 |
| WACC ≤ terminal growth rate | DCF 스킵 (수식 발산), 로그 기록 |
| 멀티플 분모 0 또는 NULL | 해당 팩터 NULL 저장 |

### 5. daily_run 업데이트

기존 `daily_run`에 밸류에이션 멀티플 갱신 단계 추가:

```text
daily_run
  -> ingest prices
  -> compute common factors
  -> compute valuation multiples    ← 추가 (fundamentals 캐시 사용, DCF는 quarterly)
  -> run screening
  -> generate research report
```

멀티플(pe_ratio 등)은 주가가 매일 반영되어야 하므로 daily 계산.
DCF는 재무제표가 분기 업데이트이므로 quarterly 계산.

### 6. quarterly_run 신규 잡

```text
croesus/jobs/quarterly_run.py
```

```bash
python -m croesus.jobs.quarterly_run
```

실행 흐름:
1. 활성 US 주식 로드 (assets 테이블).
2. FundamentalsProvider로 재무제표 수집.
3. `fundamentals` 테이블 저장/갱신.
4. 모든 종목의 DCF 재계산.
5. `valuation_snapshots` 저장.
6. `factor_values`의 `price_to_intrinsic` 업데이트.

---

## Out of Scope

- Debt-weighted WACC (전액 자기자본 CAPM으로 단순화).
- TTM EPS (최근 연간 EPS 사용, 분기 합산은 이후 개선).
- LLM DCF 가정값 오버라이드 (인터페이스 설계만, 구현은 이후).
- Quality, Growth, Leverage 팩터 (이후 스프린트).
- 비주식 자산(ETF, 채권, 크립토) 밸류에이션.
- 유료 데이터 소스 연동.
- DCF 시나리오 분석 (Bull/Base/Bear).

---

## Acceptance Criteria

### Schema

`python -m croesus.jobs.bootstrap` 실행 시:
- `fundamentals` 테이블이 생성된다.
- `valuation_snapshots` 테이블이 생성된다.

### quarterly_run

`python -m croesus.jobs.quarterly_run` 실행 시:
- 시드 종목(AAPL, MSFT, NVDA)의 재무제표가 `fundamentals`에 저장된다.
- `valuation_snapshots`에 각 종목의 DCF 결과가 저장된다.
- 데이터 부족 종목은 건너뛰고 실행이 완료된다.

### daily_run

`python -m croesus.jobs.daily_run` 실행 시:
- `factor_values`에 `pe_ratio`, `pb_ratio`, `ev_to_ebitda`, `fcf_yield`가 저장된다.
- `factor_values`에 `pe_vs_sector_pct` 등 섹터 백분위 팩터가 저장된다.
- `factor_values`에 `price_to_intrinsic`이 저장된다.

### 수동 검증

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    # 재무제표 원본
    print(conn.execute("""
        SELECT asset_id, period_end, metric_name, value
        FROM fundamentals
        WHERE metric_name IN ('free_cash_flow', 'eps')
        ORDER BY asset_id, period_end DESC
    """).df())

    # DCF 결과
    print(conn.execute("""
        SELECT asset_id, date, intrinsic_value_per_share, current_price, upside_pct, wacc
        FROM valuation_snapshots
        ORDER BY date DESC
    """).df())

    # 밸류에이션 팩터
    print(conn.execute("""
        SELECT asset_id, date, factor_name, value
        FROM factor_values
        WHERE factor_name IN ('pe_ratio', 'pb_ratio', 'pe_vs_sector_pct', 'price_to_intrinsic')
        ORDER BY asset_id, date DESC
    """).df())
```

기대 결과:
- `fundamentals`에 시드 종목의 재무 지표가 있다.
- `valuation_snapshots`에 DCF 내재 가치와 WACC가 합리적인 범위에 있다.
- `factor_values`에 밸류에이션 팩터가 있고 `pe_vs_sector_pct`는 0~100 범위다.

---

## Suggested Commit Breakdown

```text
chore: add fundamentals and valuation_snapshots tables to schema
feat: add FundamentalsProvider interface and yfinance implementation
feat: add fundamentals ingestion and repository
feat: implement valuation multiples (pe_ratio, pb_ratio, ev_to_ebitda, fcf_yield)
feat: implement sector percentile ranking for valuation factors
feat: implement CAPM WACC calculation
feat: implement 2-stage DCF with FCF growth rate estimation
feat: integrate valuation computation into daily_run
feat: add quarterly_run job entrypoint
```

---

## Notes

yfinance 재무제표 데이터는 때로 불완전하거나 레이블이 불일치한다.
`ingest_fundamentals.py`에서 yfinance 응답의 컬럼명을 내부 `metric_name` 규격으로 명시적으로 매핑한다.
매핑에 없는 컬럼은 무시하고, 없는 지표는 NULL로 저장한다.

DCF LLM 오버라이드 확장 포인트: `compute_dcf(overrides: dict | None = None)`.
`overrides`가 있으면 자동 계산값을 덮어쓰고, `assumptions_json`에 `"source": "llm_override"`로 기록한다.
