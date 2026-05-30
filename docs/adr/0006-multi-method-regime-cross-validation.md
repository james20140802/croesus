# ADR 0006: Multi-Method Regime Cross-Validation and ISM Data Source Change

## Status

Accepted

## Context

ADR 0004는 Growth × Inflation 두 축으로 거시 국면을 분류하는 3-Layer Macro Score Engine을
도입했다. 구현 과정에서 두 가지 현실적 문제가 드러났다.

### 1. ISM PMI를 FRED에서 받을 수 없다

ADR 0004 및 설계 스펙(2026-05-28)은 ISM 제조업 PMI를 FRED 코드 `MANEAPUSA`로 수집할 계획이었다.
그러나 **ISM 데이터는 2016년 6월 라이선스 분쟁으로 FRED에서 제거**되었다. `MANEAPUSA`는 사실상
빈 시계열을 반환한다. PMI는 Growth 방향 판단의 핵심 지표이므로 대체 수급 경로가 필요하다.

### 2. 단일 분류법의 편향

국면 분류 결과는 "방향(direction)을 어떻게 판단하느냐"에 민감하다. 선형회귀 기울기 투표(앙상블)
하나만 쓰면, 그 방법론 고유의 편향(예: 단기 잡음 민감도)이 결과에 그대로 반영되며 사용자는
"이 국면 판단이 방법론에 따라 달라지는가"를 알 수 없다.

## Decision

### 1. ISM 데이터는 스크래핑, CFNAI는 fallback

- ISM 제조업·서비스 PMI는 **ISM 웹사이트(ismworld.org) 직접 스크래핑**으로 수집한다
  (`croesus/macro/data_sources/ism_scraper.py`).
- 스크래핑 실패 시 **`CFNAI`(Chicago Fed National Activity Index, FRED 제공, 85개 지표 합성)** 가
  경기 활동 프록시 대체재 역할을 한다.
- `MANEAPUSA`는 코드에 fallback 경로로만 남긴다 (FRED에 데이터가 복원될 경우 대비).

### 2. Multi-Method 교차검증 (출력 전용)

1차(primary) 국면은 **언제나 앙상블 투표(Ensemble Vote)** 이며, 이것만 스크리닝에 사용된다.
이와 별도로 기관 방법론 3가지를 함께 계산하여 비교 참고용으로 제시한다
(`croesus/macro/indicators/multi_method.py`):

| 방법 | 로직 | 출처 |
|------|------|------|
| BlackRock 3M/6M MA | 3개월 MA − 6개월 MA 부호 (가속/감속) | BlackRock Investment Institute |
| Level Threshold | 절대 수준 (PMI ≥ 50, CPI YoY ≥ 3%) | 실무 관행 |
| AQR 1-Year Momentum | 현재 vs 12개월 전 수준 변화 | AQR (Brooks 2017) |

결과는 `MacroState.regime_methods`에 기록되고 `macro_scores.regime_methods` JSON 컬럼에 저장되며,
리포트에 "N/4 방법 합의" 형태로 표시된다.

**핵심 제약:** 이 3가지 대안 방법은 **출력 전용 참고 신호**다. 스크리닝 어댑터는 절대 이들을
소비하지 않는다 (ADR 0004의 단방향 의존 원칙 유지 — 스크리닝은 Ensemble Vote만 본다).

## Rationale

- ISM PMI 부재는 회피 불가능한 외부 제약이다. 스크래핑 + CFNAI 이중화로 Growth 신호의 공백을 메운다.
- 교차검증을 **출력 전용**으로 한정하면, 스크리닝 파이프라인의 결정론과 단방향 의존을 깨지 않으면서도
  "국면 판단의 견고성"이라는 메타 정보를 사용자에게 제공할 수 있다.
- 방법 간 합의도(N/4)는 그 자체로 신뢰도 신호다. 4개 방법이 만장일치면 강한 국면, 갈리면 경계선.

## Alternatives Considered

### Multi-Method를 스크리닝에 직접 반영

여러 방법의 가중 평균이나 투표를 스크리닝 파라미터에 직접 연결하는 방안.
스크리닝 로직이 복잡해지고, 어떤 방법이 결과를 움직였는지 추적이 어려워진다. 단방향 의존·결정론
원칙과도 충돌한다. → 참고 출력으로만 한정.

### ISM 스크래핑 없이 CFNAI만 사용

CFNAI 하나로 Growth를 판단하는 방안. 단순하지만 ISM PMI는 시장 참여자가 가장 주시하는 선행 지표라,
가용할 때는 쓰는 것이 신호 품질에 유리하다. → 스크래핑 우선, CFNAI fallback.

## Consequences

### Positive

- ISM 데이터 공백을 메우고 Growth 신호의 견고성을 확보.
- 국면 판단을 4가지 렌즈로 교차검증하여 경계선 상황을 식별 가능.
- 스크리닝의 단방향 의존·결정론은 그대로 유지.

### Negative

- ISM 웹사이트 스크래핑은 페이지 구조 변경 시 깨질 수 있다 (CFNAI fallback이 완충).
- 4가지 방법을 매번 계산하므로 약간의 추가 연산 비용이 든다 (무시 가능 수준).
- `regime_methods`가 리포트를 다소 복잡하게 만든다 (참고 정보이므로 수용).

## Supersedes / Relates

- ADR 0004의 데이터 소스 계획 중 `MANEAPUSA` 항목을 대체한다 (ISM 스크래퍼 + CFNAI).
- ADR 0004의 단일 Ensemble Vote 국면 분류를 보완한다 (교차검증 추가, 단 primary는 불변).
