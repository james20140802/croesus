# Sprint 002: Macro Analysis Layer

## Goal

3-Layer Macro Score Engine을 구현하여 `MacroState`를 산출하고,
이를 기존 스크리닝 파라미터 조정에 연결한다.

```text
Macro Data Ingestion
  -> 3-Layer Score Engine (Regime / Amplifier / Confirmation)
  -> MacroState (DuckDB 저장)
  -> Screening 파라미터 조정
  -> Macro Research Report
```

Sprint 001(Asset Registry + Price Ingestion)이 완료된 이후를 전제로 한다.

---

## Scope

### 1. Schema 업데이트

`macro_scores` 테이블을 `schema.sql`에 추가하고 `migrate.py`에서 반영한다.

```sql
CREATE TABLE IF NOT EXISTS macro_scores (
  date                DATE PRIMARY KEY,
  regime              TEXT NOT NULL,
  regime_confidence   DOUBLE,
  growth_direction    TEXT,
  inflation_direction TEXT,
  amplifier_score     DOUBLE,
  confirmation_score  DOUBLE,
  positioning         TEXT,
  raw_indicators      JSON,
  warnings            JSON,
  opportunities       JSON
);
```

### 2. Macro Data Sources

```text
croesus/macro/data_sources/
  fred_source.py         -- FRED API 클라이언트
  yfinance_macro.py      -- VIX, S&P 500, DXY, 원자재
  sentiment_scraper.py   -- AAII, NAAIM 스크래핑
```

**FRED 수집 지표:**

| 범주 | 코드 | 주기 |
|------|------|------|
| Growth | `CFNAI`, `UNRATE`, `ICSA`, `RSXFS`, `INDPRO`, `GDPC1` (+ ISM PMI 스크래핑) | 월/주/분기 |
| Inflation | `CPILFESL`, `PCEPILFE`, `T5YIE`, `DCOILWTICO`, `CES0500000003` | 월/일 |
| Rates | `EFFR`, `DGS2`, `DGS10`, `T10Y2Y`, `DFII10` | 일 |
| Liquidity | `WALCL`, `M2SL`, `WTREGEN`, `RRPONTSYD`, `NFCI` | 주/월 |
| Credit | `BAMLH0A0HYM2`, `BAMLC0A0CM`, `DRTSCILM` | 일/분기 |

**yfinance 수집 지표:** `^VIX`, `^VIX3M`, `^GSPC`, `DX-Y.NYB`, `KRW=X`, `HG=F`, `GC=F`, `CL=F`

**스크래핑:** AAII Bull-Bear Spread (aaii.com), NAAIM Exposure Index (naaim.org)

각 소스는 독립 모듈로 분리하고, 공통 인터페이스(`base.py` 방식)를 따른다.

### 3. Indicator Modules

```text
croesus/macro/indicators/
  growth.py       -- Layer 1: Growth 방향 (Expanding / Contracting)
  inflation.py    -- Layer 1: Inflation 방향 (Rising / Falling)
  amplifier.py    -- Layer 2: Liquidity·Credit·Rates 점수 (0~100)
  confirmation.py -- Layer 3: Volatility·Trend·Sentiment·FX 점수 (-1~+1)
```

각 모듈은 다음을 담당한다:
- 관련 지표 원본값 로드 (DuckDB의 raw 저장값에서 읽거나 직접 소스 호출).
- 5년 히스토리 기준 백분위수 정규화.
- 방향 판단 또는 점수 반환.

### 4. Macro Score Engine

```text
croesus/macro/engine.py
```

3개 레이어를 조합하여 `MacroState` dataclass를 반환한다.

```python
@dataclass
class MacroState:
    date: date
    regime: str              # Goldilocks | Reflation | Stagflation | Deflation
    regime_confidence: float
    growth_direction: str
    inflation_direction: str
    amplifier_score: float
    confirmation_score: float
    positioning: str         # Aggressive | Moderately Aggressive | Neutral | Cautious | Defensive
    warnings: list[dict]
    opportunities: list[dict]
```

Positioning 결정 규칙:

```
Goldilocks + Amplifier ≤ 30 + Confirmation > 0.3  → Aggressive
Goldilocks + Amplifier ≤ 60                        → Moderately Aggressive
Reflation  또는 Amplifier 31~60                    → Neutral
Stagflation 또는 Amplifier > 60                    → Cautious
(Stagflation + Amplifier > 60) 또는 Confirmation < -0.5 → Defensive
```

Warning / Opportunity는 LLM 없이 규칙 기반 템플릿(`templates.py`)으로 생성한다.

### 5. Screening Adapter

```text
croesus/macro/screening_adapter.py
```

`MacroState`를 받아 스크리닝 파라미터 딕셔너리를 반환한다.
스크리닝 엔진은 이 딕셔너리를 입력으로 받아 실행한다.

```python
def get_screening_params(state: MacroState) -> dict:
    """MacroState → 스크리닝 파라미터 반환 (팩터 가중치, 필터 임계값, 후보군 크기)"""
```

### 6. Macro Report Generator

```text
croesus/macro/report.py
```

`MacroState`를 받아 Markdown 리포트와 CSV를 생성한다.

출력 파일:
- `reports/macro_YYYY-MM-DD.md`
- `reports/macro_scores_YYYY-MM-DD.csv`

### 7. Job Entrypoints

```text
croesus/jobs/
  daily_macro_run.py    -- VIX, 금리, Credit Spread, RRP, FX, 원자재
  weekly_macro_run.py   -- AAII, NAAIM, Jobless Claims, TGA, Fed Balance Sheet
  monthly_macro_run.py  -- CPI, PCE, PMI, GDP, 실업률, M2
```

`daily_run.py`는 `daily_macro_run` 이후에 실행되며,
`MacroState`를 읽어 스크리닝 어댑터를 통해 조정된 파라미터로 스크리닝한다.

---

## Out of Scope

- 뉴스 LLM 분석 (향후 Layer 3 Sentiment 확장 포인트로 보류).
- 글로벌 macro 지표 (미국 시장 우선).
- Amplifier Score 가중치 백테스팅 (초기값 사용 후 추후 검증).
- 자동 트레이드 실행.

---

## Acceptance Criteria

### daily_macro_run

`python -m croesus.jobs.daily_macro_run` 실행 시:
- FRED에서 일간 지표를 수집한다.
- yfinance에서 VIX, S&P 500, FX, 원자재를 수집한다.
- `MacroState`를 계산한다.
- `macro_scores` 테이블에 저장한다.
- `reports/macro_YYYY-MM-DD.md`를 생성한다.
- 개별 소스 실패 시 해당 지표를 건너뛰고 계속 진행한다.

### 수동 검증

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("SELECT * FROM macro_scores ORDER BY date DESC LIMIT 5").df())
```

기대 결과:
- `regime` 컬럼에 유효한 국면명이 있다.
- `amplifier_score`가 0~100 범위에 있다.
- `confirmation_score`가 -1~1 범위에 있다.
- `warnings`와 `opportunities`가 JSON 배열이다.

### Screening 연동 검증

```python
from croesus.macro.engine import compute_macro_state
from croesus.macro.screening_adapter import get_screening_params

state = compute_macro_state(date.today())
params = get_screening_params(state)
print(params)
```

기대 결과: Regime에 따라 팩터 가중치가 기본값에서 조정된 딕셔너리 반환.

---

## Suggested Commit Breakdown

```text
feat: add macro_scores table to schema
feat: add FRED data source client
feat: add yfinance macro data source
feat: add AAII and NAAIM sentiment scrapers
feat: implement Layer 1 growth and inflation indicators
feat: implement Layer 2 risk amplifier
feat: implement Layer 3 confirmation
feat: implement macro score engine and MacroState
feat: add screening adapter for macro parameter adjustment
feat: add macro report generator
feat: add daily/weekly/monthly macro job entrypoints
```

---

## Notes

Amplifier Score 범주 가중치(Liquidity 35%, Credit 40%, Rates 25%)와
Positioning 임계값은 초기 경험적 추정값이다.
충분한 히스토리가 쌓인 후 실증적으로 검증하고 조정한다.
가중치와 임계값은 코드에 하드코딩하지 않고 config 파일로 분리한다.

### 구현 중 변경 사항 (2026-05-30, ADR 0006)

- **ISM PMI 수급 경로 변경:** `MANEAPUSA`는 2016년 FRED에서 제거되어 빈 시계열을 반환한다.
  ISM 제조업·서비스 PMI는 웹 스크래핑(`ism_scraper.py`)으로 수집하고, 실패 시 `CFNAI`로 대체한다.
- **Regime 교차검증 추가:** 앙상블 투표를 1차 국면으로 유지하되, BlackRock(3M/6M MA) ·
  Level Threshold · AQR(1Y momentum) 3가지 방법을 **출력 전용 참고 신호**로 함께 계산한다
  (`multi_method.py`, `MacroState.regime_methods`, `macro_scores.regime_methods` 컬럼).
  스크리닝은 여전히 앙상블 투표만 소비한다 (단방향 의존 유지).
