# 매크로 분석 레이어 상세 설명

_작성일: 2026-05-30_

---

## 개요

Croesus의 매크로 분석 레이어는 "지금 주식 시장에 투자해도 되는가?"를 판단하기 위한 선행 단계다.
개별 종목의 팩터 분석(bottom-up)에 앞서 시장 전체의 거시 환경을 수치로 평가하고,
그 결과(`MacroState`)를 이용해 종목 스크리닝의 파라미터를 동적으로 조정한다.

LLM을 사용하지 않는다. 모든 신호는 **수치 지표의 백분위수 기반 규칙**으로만 계산된다.

---

## 1. 입력: 수집하는 지표

### 1-1. yfinance (API 키 불필요)

시장에서 실시간으로 공개되는 가격 데이터. 매일 갱신.

| 티커 | 지표명 | 사용 레이어 |
|------|--------|------------|
| `^VIX` | CBOE 변동성 지수 | Layer 3 (Confirmation) |
| `^VIX3M` | 3개월 VIX | Layer 3 |
| `^GSPC` | S&P 500 지수 | Layer 3 |
| `DX-Y.NYB` | 달러 인덱스 (DXY) | Layer 3 |
| `HG=F` | 구리 선물 | Layer 3 |
| `GC=F` | 금 선물 | Layer 3 |
| `CL=F` | WTI 원유 선물 | Layer 1 (Inflation 보조) |
| `KRW=X` | 달러/원 환율 | 수집만 (현재 미사용) |

### 1-2. FRED API (API 키 필요, 무료)

미국 연방준비은행 세인트루이스(St. Louis Fed)에서 제공하는 공식 경제 통계.
키가 없으면 해당 지표 전체를 건너뛰고 계속 진행한다.

#### Growth (경기)

| FRED 코드 | 지표명 | 주기 | 사용 방법 |
|-----------|--------|------|----------|
| `CFNAI` | 시카고 Fed 전미활동지수 (85개 지표 합성) | 월 | 3개월 추세 + 0 기준선 |
| `UNRATE` | 실업률 | 월 | 3개월 추세 (하락 = 확장) |
| `ICSA` | 신규 실업수당 청구 | 주 | 4주 추세 (하락 = 확장) |
| `RSXFS` | 소매판매 | 월 | 3개월 추세 |
| `INDPRO` | 산업생산지수 | 월 | 3개월 추세 |
| `GDPC1` | 실질 GDP | 분기 | QoQ 변화율 부호 |

> **ISM PMI 관련 참고:** 초기 설계는 ISM 제조업 PMI를 FRED 코드 `MANEAPUSA`로 수집할 계획이었으나,
> ISM 데이터는 2016년 6월 라이선스 분쟁으로 **FRED에서 제거**되었다. 따라서 현재는:
> - ISM 제조업·서비스 PMI는 **ISM 웹사이트 직접 스크래핑**으로 수집한다 (아래 1-3 참조).
> - 스크래핑 실패 시 **`CFNAI`(FRED 기반)** 가 경기 활동 프록시 대체재 역할을 한다.
> - `MANEAPUSA`는 코드에 fallback으로만 남아 있으며 실제로는 비어 있을 가능성이 높다.
> 자세한 결정 근거는 ADR 0006 참조.

#### Inflation (물가)

| FRED 코드 | 지표명 | 주기 | 사용 방법 |
|-----------|--------|------|----------|
| `CPILFESL` | Core CPI (식품·에너지 제외) | 월 | 3개월 추세 |
| `PCEPILFE` | Core PCE (연준 선호 물가지표) | 월 | 3개월 추세 |
| `T5YIE` | 5년 기대인플레이션 (BEI) | 일 | 5일 추세 |
| `DCOILWTICO` | WTI 유가 | 일 | 5일 추세 (상품가격 압력 프록시) |
| `CES0500000003` | 민간 임금상승률 | 월 | 3개월 추세 |

#### Rates (금리)

| FRED 코드 | 지표명 | 주기 | 사용 방법 |
|-----------|--------|------|----------|
| `EFFR` | 연방기금금리 (기준금리) | 일 | 5년 백분위수 |
| `DGS2` | 2년물 국채 금리 | 일 | 수집만 |
| `DGS10` | 10년물 국채 금리 | 일 | 수집만 |
| `T10Y2Y` | 장단기 금리차 (10Y-2Y) | 일 | 5년 백분위수 (낮을수록 위험) |
| `DFII10` | 실질금리 (TIPS 10년) | 일 | 5년 백분위수 |

#### Liquidity (유동성)

| FRED 코드 | 지표명 | 주기 | 사용 방법 |
|-----------|--------|------|----------|
| `WALCL` | Fed 대차대조표 (총자산) | 주 | 5년 백분위수 (줄면 위험) |
| `M2SL` | M2 통화량 | 월 | 5년 백분위수 (줄면 위험) |
| `WTREGEN` | TGA (재무부 현금계좌) | 주 | 수집만 |
| `RRPONTSYD` | 역레포 잔액 (RRP) | 일 | 5년 백분위수 (늘면 위험) |
| `NFCI` | 시카고 Fed 금융환경지수 | 주 | 5년 백분위수 (높으면 위험) |

#### Credit (신용)

| FRED 코드 | 지표명 | 주기 | 사용 방법 |
|-----------|--------|------|----------|
| `BAMLH0A0HYM2` | 하이일드 스프레드 | 일 | 5년 백분위수 |
| `BAMLC0A0CM` | 투자등급 스프레드 | 일 | 5년 백분위수 |
| `DRTSCILM` | 은행 상업·산업 대출태도 | 분기 | 5년 백분위수 |

### 1-3. 웹 스크래핑

| 지표 | 출처 | 주기 | 사용 레이어 | 의미 |
|------|------|------|------------|------|
| ISM 제조업 PMI | ismworld.org | 월 | Layer 1 (Growth) | 제조업 경기 확장/수축 (≥50 = 확장) |
| ISM 서비스 PMI | ismworld.org | 월 | Layer 1 (Growth) | 서비스업 경기 확장/수축 |
| AAII Bull-Bear Spread | aaii.com | 주 | Layer 3 (Sentiment) | 개인투자자 강세 비율 - 약세 비율 |
| NAAIM Exposure Index | naaim.org | 주 | Layer 3 (Sentiment) | 전문 자산운용사의 평균 주식 노출 비중 |

> **주의:** 스크래핑은 소스 페이지 구조가 바뀌면 실패할 수 있다. 실패 시 해당 지표를 건너뛰고 계속 진행한다.
> ISM PMI 스크래핑이 실패하면 Growth 판단은 `CFNAI`(FRED)로 대체된다.

---

## 2. 분석 방법: 3-Layer 처리

원시 데이터를 받아 세 단계를 순서대로 통과하면서 `MacroState`를 만들어낸다.

```
원시 데이터
  ↓
Layer 1: Regime 분류  (Growth × Inflation → 4가지 국면)
  ↓
Layer 2: Risk Amplifier  (국면 내 강도 조정, 0~100)
  ↓
Layer 3: Confirmation  (시장 신호 일치도, -1.0~+1.0)
  ↓
Positioning 결정  (5단계)
  ↓
스크리닝 파라미터 조정
```

---

### Layer 1: Regime 분류

#### Growth 방향 판단

ISM 제조업 PMI, ISM 서비스 PMI, CFNAI, 실업률, 실업수당, 소매판매, 산업생산, GDP 등
각 지표가 **독립적으로 한 표씩 투표**한다. (ISM PMI는 스크래핑으로, CFNAI는 FRED로 수급하며,
스크래핑 실패 시 CFNAI가 경기 활동 프록시를 보완한다 — 위 1-2/1-3 및 ADR 0006 참조.)

```
각 지표의 최근 N개월 데이터에 선형회귀를 적용 → 기울기(slope) 계산
  기울기 > 0 (상승 추세)  → Expanding 한 표
  기울기 < 0 (하락 추세)  → Contracting 한 표
  (PMI는 추가로 50 기준선, CFNAI는 0 기준선도 별도 투표)

다수결 → Expanding / Contracting
Confidence = 다수 측 표 수 / 전체 표 수
```

예시: 제조업 PMI 상승(1표) + 제조업 PMI≥50(1표) + 실업률 하락(1표) + 소매판매 상승(1표)
= 4표 중 4표 Expanding → Confidence 1.0

#### Inflation 방향 판단

Core CPI, Core PCE, 기대인플레이션, WTI, 임금 각각이 동일하게 투표한다.

```
기울기 > 0 → Rising 한 표
기울기 < 0 → Falling 한 표

다수결 → Rising / Falling
```

#### 국면 결합

| | Inflation Rising | Inflation Falling |
|---|---|---|
| **Growth Expanding** | 🔴 Reflation | 🟢 Goldilocks |
| **Growth Contracting** | 🔴 Stagflation | 🟡 Deflation |

**Regime Confidence** = Growth Confidence와 Inflation Confidence의 평균.
두 신호가 모두 일치할 때 1.0에 가까워지고, 경계선에서 갈릴 때 0.5에 가까워진다.

#### Regime 교차검증 (Multi-Method)

위의 **앙상블 투표(Ensemble Vote)** 가 스크리닝에 실제로 쓰이는 1차(primary) 국면이다.
그러나 단일 방법론에만 의존하면 방법론 자체의 편향이 드러나지 않는다. 이를 보완하기 위해
기관 투자자들이 쓰는 **3가지 대안 분류법을 함께 계산**하여 참고용으로 비교 제시한다.

| 방법 | type | 로직 | 출처 |
|------|------|------|------|
| **Ensemble Vote** (primary) | `ensemble_vote` | 모든 가용 지표의 다수결 (위 설명) | Croesus 기본 |
| BlackRock 3M/6M MA | `direction_momentum` | 3개월 이동평균 − 6개월 이동평균의 부호 (가속/감속) | BlackRock Investment Institute |
| Level Threshold | `level` | 절대 수준 임계값 (PMI ≥ 50, CPI YoY ≥ 3%) | 실무 관행 |
| AQR 1-Year Momentum | `yearly_momentum` | 현재 수준 vs 12개월 전 수준의 변화 | AQR, "A Half Century of Macro Momentum" (2017) |

**중요 — 단방향 보장:** 이 3가지 대안 방법은 **출력 전용 참고 신호**다. `MacroState.regime_methods`에
기록되어 리포트에 "N/4 방법이 동일 국면에 합의" 형태로 표시될 뿐, **스크리닝 파라미터 결정에는
절대 사용되지 않는다** (스크리닝은 언제나 Ensemble Vote만 소비). 방법 간 불일치가 크면 현재 국면이
경계선에 있다는 신호로 해석한다.

각 방법은 동일한 Growth × Inflation 4국면 분류표를 공유하되, Growth/Inflation **방향을 판단하는
방식만** 다르다. 데이터가 부족하면 해당 방법은 중립값(Expanding/Rising, confidence 0.5)으로 fallback한다.

---

### Layer 2: Risk Amplifier

같은 Goldilocks라도 금리가 극단적으로 높거나 신용시장이 얼어붙어 있으면 공격적으로 투자하기 어렵다. 이 레이어는 **현재 금융환경의 스트레스 강도**를 0~100 숫자로 표현한다.

#### 백분위수 정규화

각 지표의 현재값을 **5년 히스토리 대비 백분위수**로 변환한다.

```python
percentile = (지난 5년 데이터 중 현재값보다 낮은 값의 수) / 전체 데이터 수 × 100
```

지표 특성에 따라 위험 방향이 다르다:
- "높을수록 위험" (HY 스프레드, VIX 등): risk_score = percentile
- "낮을수록 위험" (Fed 대차대조표, 장단기 금리차 등): risk_score = 100 - percentile

이렇게 하면 모든 지표가 "높을수록 위험"이라는 동일한 방향성을 갖게 된다.

#### 범주별 집계

```
Liquidity Score = 평균(WALCL, M2SL, RRPONTSYD, NFCI의 risk_score)
Credit Score    = 평균(HY Spread, IG Spread, 은행 대출태도의 risk_score)
Rates Score     = 평균(실질금리, 장단기금리차, 기준금리의 risk_score)

Amplifier Score = Liquidity × 35% + Credit × 40% + Rates × 25%
```

Credit에 가장 높은 가중치(40%)를 준 이유: 신용시장의 경색은 실제 기업 자금조달에 직접 영향을 미치고, 역사적으로 시장 하락과의 선행 관계가 가장 명확하다.

#### 데이터 없을 때

해당 지표의 시계열이 없으면 해당 범주는 50.0(중립)으로 처리한다. FRED 키가 없는 현재 상태에서는 세 범주 모두 50.0이 되어 Amplifier = 50.0이 된다.

---

### Layer 3: Confirmation

"Layer 1에서 내린 국면 판단이 현재 시장 지표들과 일치하는가?"를 검증한다.

일치하면 양수(+), 불일치하면 음수(-), 평균을 내면 -1.0~+1.0.

#### 검증 방식 (Goldilocks 기준 예시)

| 지표 | 확인 로직 | 점수 |
|------|----------|------|
| VIX 수준 | 5년 역사 대비 낮은 구간 → 공포 없음 = Goldilocks 확인 | +1에 가까움 |
| VIX3M/VIX 비율 | 비율 > 1 → 단기 공포 없음 = Goldilocks 확인 | 비율에 비례 |
| S&P 500 vs 200MA | 200일 이평선 위 → 추세 강세 = Goldilocks 확인 | +1.0 or -1.0 |
| AAII Bull-Bear | 적당한 낙관 → 확인 / 극단 낙관(80th↑) → 과열 경고 | +/-0.5 |
| Copper/Gold 비율 | 상승 추세 → 성장 기대 = Goldilocks 확인 | 백분위수에 비례 |
| DXY 달러 강세 | 낮은 구간 → 글로벌 유동성 우호 = Goldilocks 확인 | +에 가까움 |

Stagflation이라면 위 모든 기준이 반전된다 (VIX 높음 = 확인, S&P 200MA 아래 = 확인 등).

---

### Positioning 결정

세 레이어의 결과를 아래 규칙 테이블에 순서대로 적용, **첫 번째 일치하는 규칙**을 선택한다.

| 우선순위 | 조건 | Positioning |
|----------|------|-------------|
| 1 | Stagflation + Amplifier > 60 | Defensive |
| 2 | Confirmation < -0.5 (국면 무관) | Defensive |
| 3 | Stagflation | Cautious |
| 4 | Amplifier > 60 | Cautious |
| 5 | Goldilocks + Amplifier ≤ 30 + Confirmation > 0.3 | Aggressive |
| 6 | Goldilocks + Amplifier ≤ 60 | Moderately Aggressive |
| 7 | Reflation | Neutral |
| 8 | Amplifier 31~60 | Neutral |
| 9 | 그 외 | Neutral |

---

## 3. 출력값과 의미

### MacroState 필드

| 필드 | 타입 | 범위 | 의미 |
|------|------|------|------|
| `regime` | 문자열 | Goldilocks / Reflation / Stagflation / Deflation | 현재 거시 국면 |
| `regime_confidence` | 실수 | 0.0 ~ 1.0 | 국면 판단의 신뢰도. 낮으면 경계선에 있다는 뜻 |
| `growth_direction` | 문자열 | Expanding / Contracting | 경기 확장/수축 방향 |
| `inflation_direction` | 문자열 | Rising / Falling | 물가 상승/하락 방향 |
| `amplifier_score` | 실수 | 0 ~ 100 | 금융환경 스트레스 강도. 높을수록 위험 |
| `confirmation_score` | 실수 | -1.0 ~ +1.0 | 시장 신호와 국면 판단의 일치도 |
| `positioning` | 문자열 | 5단계 | 권장 투자 강도 |
| `warnings` | 리스트 | — | 규칙 기반 경고 (지표명, 현재값, 백분위수) |
| `opportunities` | 리스트 | — | 규칙 기반 기회 신호 |
| `raw_indicators` | 딕셔너리 | — | 각 지표의 마지막 수집값 + 레이어별 중간 점수 |
| `regime_methods` | 딕셔너리 | — | 4가지 분류법(vote/blackrock/level/aqr_momentum)의 국면 판단 결과. 교차검증용 참고 신호 (스크리닝 미사용) |

> `regime_methods`는 `macro_scores` 테이블에 `regime_methods JSON` 컬럼으로 함께 저장된다.

### Positioning별 의미와 스크리닝 효과

#### 🟢 Aggressive
- **조건:** Goldilocks + Amplifier ≤ 30 + Confirmation > 0.3
- **의미:** 성장은 확장 중, 물가는 안정, 금융환경 우호적, 시장 신호도 일치. 최적의 투자 환경.
- **스크리닝 효과:** 모멘텀 가중치 +10%p, 변동성 페널티 -5%p, 후보 종목 최대 26개

#### 🟡 Moderately Aggressive
- **조건:** Goldilocks + Amplifier ≤ 60
- **의미:** 좋은 환경이지만 금융환경이 완전히 우호적이지는 않거나 시장 신호가 약함. 적극적이되 주의 필요.
- **스크리닝 효과:** 모멘텀 가중치 +10%p, 후보 종목 기본값 유지

#### ⚪ Neutral
- **조건:** Reflation, 또는 Amplifier 31~60
- **의미:** 성장은 있지만 물가 부담이 있거나, 금융환경이 중립적. 특정 방향으로 치우치지 않는 것이 적절.
- **스크리닝 효과:** Reflation 시 유동성 가중치 +10%p (원자재·가치주 선호), 기본 파라미터 유지

#### 🟠 Cautious
- **조건:** Stagflation, 또는 Amplifier > 60
- **의미:** 경기 둔화 + 물가 상승의 최악 조합이거나, 금융환경이 극단적으로 타이트. 방어적 접근 필요.
- **스크리닝 효과:** 최소 유동성 1.5배, 최대 허용 변동성 0.8배, 최소 시총 2배 (대형 우량주 위주)

#### 🔴 Defensive
- **조건:** Stagflation + Amplifier > 60, 또는 Confirmation < -0.5
- **의미:** 최악의 스트레스 조합이거나, 국면 판단과 시장 신호가 극단적으로 불일치. 위기 가능성.
- **스크리닝 효과:** Cautious의 필터 + 후보 종목 최소 14개로 압축. 분산보다 집중 방어.

### 리포트 파일

#### `reports/macro_YYYY-MM-DD.md`
사람이 읽기 위한 요약. 레이어별 근거, 경고/기회, 스크리닝 조정 내용 포함.

#### `reports/macro_scores_YYYY-MM-DD.csv`
날짜별 시계열로 누적 사용 가능. 국면 변화 추적 및 사후 검증용.

---

## 4. 전제와 한계

### 설계 전제

**① 수준(level)이 아닌 방향(direction)이 중요하다**
PMI가 48이든 52든 수준 자체보다, 지난 3개월간 올라가고 있는지 내려가고 있는지가 스크리닝 타이밍에 더 중요하다. 같은 이유로 CPI 수치 자체가 아닌 그 추세를 본다.

**② 5년 히스토리가 정규화의 기준이다**
각 지표의 현재값이 "최근 5년 중 어느 위치인가"로 환산된다. 이는 절대 수준이 아닌 상대 수준으로 판단한다는 뜻이다. 5년이라는 기간은 대부분의 경기 사이클(평균 3~5년)을 포함하면서도 너무 오래된 구조적 변화(예: 2008년 이전 저금리 시대)가 기준을 왜곡하지 않도록 한 타협점이다.

**③ 개별 지표는 불완전하지만 앙상블은 강하다**
PMI 하나만 보면 잡음이 많다. 6개 지표가 각자 독립적으로 투표하고 다수결로 결정하는 구조는, 특정 지표의 일시적 왜곡이나 데이터 지연이 전체 판단을 뒤집지 못하도록 한다.

**④ Macro와 종목 분석은 단방향 의존이다**
MacroState는 스크리닝 파라미터만 조정하고, 종목 선별 자체는 팩터 엔진이 한다. 매크로 모듈은 스크리닝 엔진을 알지 못한다. 이 분리 덕분에 어느 한쪽을 교체하거나 개선해도 다른 쪽에 영향이 없다.

### 알려진 한계

**① Amplifier가 50.0인 경우 FRED 키가 없는 것이다**
FRED 데이터 없이는 Liquidity, Credit, Rates 모두 히스토리가 없어 중립값(50.0)이 할당된다. Amplifier = 50.0이 나오면 신뢰하지 말 것.

**② Growth 판단이 후행할 수 있다**
GDP는 분기 후행, CPI/PCE는 월 후행 데이터다. 경기 전환점에서 실제 변화가 일어나고 1~2개월 후에야 국면이 바뀔 수 있다. 일간 데이터(BEI, VIX, Credit Spread)가 선행 신호 역할을 어느 정도 보완하지만 완전하지 않다.

**③ 가중치는 경험적 초기값이다**
Amplifier의 범주 가중치(Liquidity 35%, Credit 40%, Rates 25%)와 Positioning 임계값들은 금융 실무의 관행을 참고한 초기 추정값이다. 충분한 히스토리가 쌓인 후 실증적으로 검증하고 조정해야 한다. 조정 시 `config.yaml`만 수정하면 된다.

**④ Confirmation이 현재 국면에 조건부다**
동일한 VIX 수치도 Goldilocks 국면에서는 긍정 신호, Stagflation 국면에서는 부정 신호로 해석된다. 즉 Layer 3는 Layer 1의 판단에 의존한다. Layer 1이 틀렸다면 Layer 3도 잘못된 방향으로 증폭될 수 있다.

**⑤ 스크래핑은 불안정하다**
AAII와 NAAIM 스크래핑은 소스 웹사이트 구조가 바뀌면 작동을 멈춘다. 실패 시 해당 지표는 조용히 건너뛰어지므로 Confirmation 계산에서 제외된 채 결과가 나온다.

**⑥ 뉴스·텍스트 분석은 미포함이다**
BlackRock의 MLP(Macro Language Processing)처럼 뉴스와 애널리스트 리포트에서 매크로 신호를 추출하는 기능은 현재 범위 밖이다. Layer 3의 Sentiment 범주는 AAII/NAAIM 정량 지표만 사용한다. 향후 LLM 기반 뉴스 분석으로 보완할 수 있도록 모듈이 분리되어 있다.

---

## 5. 업데이트 주기

| 잡 | 명령어 | 수집 지표 | 권장 실행 |
|----|--------|----------|----------|
| `daily_macro_run` | `python -m croesus.jobs.daily_macro_run` | yfinance 전체 + FRED 일간 지표 | 매 거래일 장 시작 전 |
| `weekly_macro_run` | `python -m croesus.jobs.weekly_macro_run` | 일간 + FRED 주간 + AAII/NAAIM | 매주 금요일 또는 월요일 |
| `monthly_macro_run` | `python -m croesus.jobs.monthly_macro_run` | 전체 (일간+주간+월간+분기) | 매월 초 주요 지표 발표 후 |

월간·분기 지표(CPI, GDP 등)는 발표 주기가 느리므로, 마지막 발표값을 재사용해 매일의 MacroState 계산에 포함된다. 즉 `daily_macro_run`을 매일 돌려도 월간 지표는 가장 최근 발표값 기준으로 자동 반영된다.

---

## 6. 검증 방법

```python
# MacroState 직접 확인
from croesus.db.connection import get_connection

with get_connection() as conn:
    df = conn.execute("SELECT * FROM macro_scores ORDER BY date DESC LIMIT 5").df()
    print(df)

# 정상 상태:
# - regime 컬럼에 유효한 국면명
# - amplifier_score가 0~100 범위 (50.0이면 FRED 데이터 없음)
# - confirmation_score가 -1~1 범위
# - raw_indicators에 ^VIX, ^GSPC 등이 보임
```

```python
# 스크리닝 파라미터 확인
from datetime import date
from croesus.macro.engine import compute_macro_state
from croesus.macro.screening_adapter import get_screening_params

state = compute_macro_state(date.today())
params = get_screening_params(state)
print(params)

# 기대 결과: regime에 따라 factor_weights가 기본값에서 조정된 딕셔너리
```
