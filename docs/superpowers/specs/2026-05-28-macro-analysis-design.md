# Macro Analysis Layer — Design Spec
_2026-05-28_

## Summary

Croesus의 기존 bottom-up 종목 스크리닝 앞단에 macro 분석 레이어를 추가한다.
이 레이어는 "지금 주식 시장에 투자해도 되는가?"를 판단하는 선행 단계로서,
3개의 레이어(Regime → Risk Amplifier → Confirmation)를 통해 `MacroState`를 산출하고,
이를 스크리닝 파라미터 조정에 사용한다.

---

## Background

기존 Croesus 파이프라인은 순수 bottom-up 방식이다.
개별 종목의 모멘텀·변동성·유동성 팩터를 계산해 랭킹을 내지만,
시장 전체의 과열·버블·위기 여부를 고려하지 않는다.

실무 투자 기관(Bridgewater, BlackRock, AQR 등)은 공통적으로:
1. **Growth × Inflation 두 축**으로 거시 국면(regime)을 분류한다.
2. 유동성·신용·변동성·심리 지표를 보조 수정자(modifier)로 활용한다.
3. 국면별로 팩터 비중과 종목 필터를 동적으로 조정한다.

이 설계는 해당 방법론을 Croesus에 적용한다.

---

## Architecture

```
[추가]
Macro Data Ingestion ──────────────────────────────────┐
  → Macro Score Engine (3-Layer)                       │
  → MacroState                                         ▼
                                           Screening (파라미터 조정)
[기존]                                               ↑
Asset Universe → Data Ingestion → Factor Engine ─────┘
                                                     ↓
                                           통합 Research Report
```

**설계 원칙:**
- Macro 모듈은 종목 분석과 완전히 분리된 독립 모듈이다.
- `MacroState`는 DuckDB `macro_scores` 테이블에 날짜별로 저장된다.
- 스크리닝 엔진은 `MacroState`를 읽어 파라미터를 조정하지만, macro 모듈은 스크리닝을 알지 못한다 (단방향 의존).
- LLM을 사용하지 않는다. 모든 신호는 수치 지표의 백분위수 기반 규칙으로 계산한다.

---

## Update Cadence

지표마다 갱신 주기가 다르므로 잡을 분리한다.

```
daily_macro_run    → VIX, 금리, Credit Spread, RRP, S&P 500, FX, 원자재
weekly_macro_run   → AAII Sentiment, NAAIM Exposure, Jobless Claims, Fed Balance Sheet, TGA
monthly_macro_run  → CPI, PCE, PMI, GDP, 실업률, M2, 임금상승률
```

월간·주간 지표는 마지막 발표값을 재사용하여 매일의 `MacroState` 계산에 포함한다.

---

## Data Sources

### FRED (무료, API 키 필요)

| 범주 | 지표 | FRED 코드 | 주기 |
|------|------|-----------|------|
| **Growth** | GDP 성장률 | `GDPC1` | 분기 |
| | ISM Manufacturing PMI | `MANEAPUSA` | 월 |
| | 실업률 | `UNRATE` | 월 |
| | 신규 실업수당 청구 | `ICSA` | 주 |
| | 소매판매 | `RSXFS` | 월 |
| | 산업생산 | `INDPRO` | 월 |
| **Inflation** | CPI | `CPIAUCSL` | 월 |
| | Core CPI | `CPILFESL` | 월 |
| | PCE | `PCEPI` | 월 |
| | Core PCE | `PCEPILFE` | 월 |
| | 기대인플레이션 (5Y BEI) | `T5YIE` | 일 |
| | WTI 유가 | `DCOILWTICO` | 일 |
| | 임금상승률 | `CES0500000003` | 월 |
| **Rates** | 기준금리 | `EFFR` | 일 |
| | 2Y Treasury | `DGS2` | 일 |
| | 10Y Treasury | `DGS10` | 일 |
| | Yield Curve (10Y-2Y) | `T10Y2Y` | 일 |
| | 실질금리 (TIPS 10Y) | `DFII10` | 일 |
| **Liquidity** | Fed Balance Sheet | `WALCL` | 주 |
| | M2 | `M2SL` | 월 |
| | TGA | `WTREGEN` | 주 |
| | RRP | `RRPONTSYD` | 일 |
| | 금융환경지수 (NFCI) | `NFCI` | 주 |
| **Credit** | HY Spread | `BAMLH0A0HYM2` | 일 |
| | IG Spread | `BAMLC0A0CM` | 일 |
| | 대출태도지수 | `DRTSCILM` | 분기 |
| **Earnings** | 기업이익률 | `CPATAX` | 분기 |

### yfinance (무료)

| 범주 | 지표 | 티커 | 주기 |
|------|------|------|------|
| **Volatility** | VIX | `^VIX` | 일 |
| | VIX 3M | `^VIX3M` | 일 |
| **Market Trend** | S&P 500 | `^GSPC` | 일 |
| **FX** | DXY | `DX-Y.NYB` | 일 |
| | USD/KRW | `KRW=X` | 일 |
| **Commodities** | WTI | `CL=F` | 일 |
| | 구리 | `HG=F` | 일 |
| | 금 | `GC=F` | 일 |

### 웹 스크래핑 (무료, 불안정 가능성)

| 지표 | 소스 | 주기 |
|------|------|------|
| AAII Bull-Bear Spread | aaii.com | 주 |
| NAAIM Exposure Index | naaim.org | 주 |

---

## 3-Layer Scoring Framework

### Layer 1: Regime Classification

Growth와 Inflation 각각의 **방향(direction)**을 판단한다. 수준(level)이 아닌 모멘텀이 중요하다.

```
Growth 방향:    ISM PMI 3개월 추세 + GDP QoQ 변화 + 실업률 방향 + 소매판매
                → Expanding / Contracting

Inflation 방향: Core CPI 3개월 추세 + Core PCE + 기대인플레이션(T5YIE)
                → Rising / Falling
```

**국면 분류표:**

|                      | Inflation Rising | Inflation Falling |
|----------------------|-----------------|-------------------|
| **Growth Expanding** | 🔴 Reflation    | 🟢 Goldilocks     |
| **Growth Contracting**| 🔴 Stagflation  | 🟡 Deflation      |

**국면별 기본 포지셔닝:**

| 국면 | 성격 | 스크리닝 방향 |
|------|------|-------------|
| Goldilocks | 성장↑ 물가↓ | 적극적, 성장·모멘텀 선호 |
| Reflation | 성장↑ 물가↑ | 중립, 원자재·가치주 선호 |
| Stagflation | 성장↓ 물가↑ | 방어적, 고변동성 종목 제외 |
| Deflation | 성장↓ 물가↓ | 신중, 방어주·배당주 / 역발상 탐색 |

**`regime_confidence`:**
Growth 신호와 Inflation 신호 각각의 구성 지표들이 얼마나 일관된 방향을 가리키는지로 계산한다 (0.0~1.0).

---

### Layer 2: Risk Amplifier

같은 Goldilocks라도 신용·유동성·금리 환경에 따라 공격성 강도를 조정한다.

**입력 지표 및 범주:**

| 범주 | 지표 |
|------|------|
| Liquidity | WALCL 증감율, M2 증감율, RRP, TGA, NFCI |
| Credit | HY Spread, IG Spread, Jobless Claims |
| Rates | 실질금리(DFII10), Yield Curve(T10Y2Y), 기준금리 방향 |

**정규화 방법:**

```
각 지표 → 5년 히스토리 기준 백분위수 (0~100)
위험 방향에 따라 조정:
  "높을수록 위험" → risk_score = percentile
  "낮을수록 위험" → risk_score = 100 - percentile

범주 점수 = 해당 범주 지표들의 단순 평균
Amplifier Score = 3개 범주의 가중 평균 (Liquidity 35%, Credit 40%, Rates 25%)
```

**Amplifier Score 해석 및 스크리닝 효과:**

| Score | 구간 | 스크리닝 조정 |
|-------|------|-------------|
| 0~30 | 우호적 | 팩터 제약 완화, 더 공격적 |
| 31~60 | 중립 | 기본 파라미터 유지 |
| 61~100 | 스트레스 | 최소 유동성 1.5×, 최대 변동성 0.8×, 최소 시총 2.0× |

---

### Layer 3: Confirmation

국면과 보조 지표들이 일치하는지 확인한다. 일치하면 신호 강화, 불일치하면 경고.

**입력 지표 및 범주:**

| 범주 | 지표 |
|------|------|
| Volatility | VIX, VIX3M/VIX ratio (term structure), Realized Vol |
| Market Trend | S&P 500 vs 200MA, 52주 고점 대비, Breadth (Advance/Decline) |
| Sentiment | AAII Bull-Bear, Put/Call Ratio, NAAIM Exposure |
| FX & Commodities | DXY, Copper/Gold Ratio, WTI |

**계산 방법:**

각 지표가 현재 Regime과 일치하는지를 -1.0 ~ +1.0으로 점수화한 뒤 평균한다.

```
Goldilocks 국면에서:
  VIX 낮음 (안전 구간) → +1.0
  AAII 극단 낙관      → -0.5  (과열 경고 = 불일치)
  Copper/Gold 상승    → +0.8
  → Confirmation Score = 평균(+1.0, -0.5, +0.8, ...) → +0.65
```

**뉴스 LLM 분석 (향후 확장 포인트):**
현재는 정량 지표만 사용하나, 향후 뉴스·리포트 크롤링 후 LLM 분석으로
Sentiment 범주를 보완할 수 있다. BlackRock의 Macro Language Processing(MLP) 플랫폼이
유사한 방식으로 브로커 노트에서 macro 신호를 추출한다.

**Confirmation Score 효과:**

```python
# 기본 후보군 크기 20개
candidate_count = int(20 × (1 + confirmation_score × 0.3))
# +1.0 → 26개, 0.0 → 20개, -1.0 → 14개
```

---

## MacroState Output

```python
@dataclass
class MacroState:
    date: date

    # Layer 1
    regime: str              # "Goldilocks" | "Reflation" | "Stagflation" | "Deflation"
    regime_confidence: float # 0.0 ~ 1.0
    growth_direction: str    # "Expanding" | "Contracting"
    inflation_direction: str # "Rising" | "Falling"

    # Layer 2
    amplifier_score: float   # 0 ~ 100 (높을수록 스트레스)

    # Layer 3
    confirmation_score: float  # -1.0 ~ +1.0

    # 파생
    positioning: str         # "Aggressive" | "Moderately Aggressive" | "Neutral" | "Cautious" | "Defensive"

    # 규칙 기반 알림 (LLM 없음, 템플릿 기반)
    warnings: list[dict]     # {"indicator", "current", "percentile", "code"}
    opportunities: list[dict]
```

**Positioning 결정 규칙:**

```
Goldilocks + Amplifier ≤ 30 + Confirmation > 0.3  → Aggressive
Goldilocks + Amplifier ≤ 60                        → Moderately Aggressive
Reflation  또는 Amplifier 31~60                    → Neutral
Stagflation 또는 Amplifier > 60                    → Cautious
(Stagflation + Amplifier > 60) 또는 Confirmation < -0.5 → Defensive
```

---

## Screening Integration

`MacroState`가 기존 스크리닝의 파라미터를 조정한다. 스크리닝 로직 자체는 변경하지 않는다.

**① 팩터 가중치 조정 (Regime 기반)**

```python
base_weights = {
    "momentum": 0.35,
    "liquidity": 0.25,
    "trend": 0.25,
    "volatility_penalty": 0.15,
}

regime_overrides = {
    "Goldilocks":  {"momentum": +0.10, "volatility_penalty": -0.05},
    "Reflation":   {"momentum": -0.05, "liquidity": +0.10},
    "Stagflation": {"momentum": -0.15, "volatility_penalty": +0.15},
    "Deflation":   {"momentum": -0.10, "liquidity": +0.05},
}
```

**② 종목 필터 임계값 조정 (Amplifier Score 기반)**

```python
if amplifier_score > 60:
    min_liquidity_usd  *= 1.5
    max_volatility_3m  *= 0.8
    min_market_cap_usd *= 2.0
```

**③ 후보군 크기 조정 (Confirmation Score 기반)**

```python
candidate_count = int(base_count × (1 + confirmation_score × 0.3))
```

---

## Report Output

### Markdown (`reports/macro_YYYY-MM-DD.md`)

```
# Macro Research Report — YYYY-MM-DD

## Current Regime: [emoji] [regime]
> [growth_direction] Growth + [inflation_direction] Inflation
> Confidence: XX% | Positioning: [positioning]

## Layer 1: Regime
[Growth / Inflation 방향 근거 지표 테이블]

## Layer 2: Risk Amplifier — Score XX/100
[Liquidity / Credit / Rates 범주별 점수 테이블]

## Layer 3: Confirmation — Score +X.XX
[Volatility / Market Trend / Sentiment / FX&Commodities 테이블]

## Warnings
[규칙 기반 템플릿: 지표명, 현재값, 백분위수]

## Opportunities
[규칙 기반 템플릿: 지표명, 현재값, 백분위수]

## Screening Adjustments Applied
[팩터 가중치 변화, 필터 변화, 후보군 크기]
```

### CSV (`reports/macro_scores_YYYY-MM-DD.csv`)

날짜별 시계열로 누적하여 Regime 변화 추적에 사용한다.

```
date, regime, regime_confidence, amplifier_score, confirmation_score,
positioning, ism_pmi, core_cpi, hy_spread, vix, aaii_bull_bear, ...
```

---

## Database Schema

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
    raw_indicators      JSON,   -- 개별 지표 원본값 저장
    warnings            JSON,
    opportunities       JSON
);
```

---

## Repository Structure

```
croesus/
  macro/
    data_sources/
      fred_source.py          -- FRED API 클라이언트
      yfinance_macro.py       -- VIX, S&P500, FX, 원자재
      sentiment_scraper.py    -- AAII, NAAIM 스크래핑
    indicators/
      growth.py               -- Layer 1 Growth 신호
      inflation.py            -- Layer 1 Inflation 신호
      amplifier.py            -- Layer 2 Liquidity·Credit·Rates
      confirmation.py         -- Layer 3 Volatility·Trend·Sentiment·FX
    engine.py                 -- MacroState 산출 (3-Layer 조합)
    screening_adapter.py      -- MacroState → 스크리닝 파라미터 변환
    report.py                 -- Markdown·CSV 리포트 생성
    templates.py              -- 규칙 기반 Warning·Opportunity 템플릿

  jobs/
    daily_macro_run.py        -- 기존 + macro 통합
    weekly_macro_run.py       -- 주간 지표 갱신
    monthly_macro_run.py      -- 월간 지표 갱신
```

---

## Out of Scope

- 뉴스·리포트 크롤링 후 LLM 분석 (향후 Layer 3 Sentiment 확장 포인트로 보류)
- 자산 배분 자동화 (MacroState는 참고 자료, 트레이드 실행은 사용자 승인 필요)
- 글로벌 macro (초기 범위는 미국 시장)
- 실시간 데이터 (일간 업데이트로 충분)
- 백테스팅 (macro 신호 유효성 검증은 별도 실험으로)

---

## Open Questions

- Amplifier Score 범주 가중치 (Liquidity 35%, Credit 40%, Rates 25%)는 실증 검증 전 초기값이다. 추후 데이터 기반으로 조정 필요.
- AAII·NAAIM 스크래핑은 소스 구조 변경 시 깨질 수 있다. 안정성 모니터링 필요.
- Earnings 범주 (FRED `CPATAX`)는 분기 후행 데이터라 실시간 유효성이 낮다. 현재 3개 레이어 어디에도 배치하지 않고 리포트 보조 참고용으로만 표시한다. 향후 Amplifier의 Credit 범주에 편입하거나, EPS revision 데이터(유료) 확보 시 독립 레이어로 승격 여부를 검토한다.
