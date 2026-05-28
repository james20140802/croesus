# ADR 0004: Add Macro Analysis Layer

## Status

Accepted

## Context

Croesus의 기존 파이프라인은 순수 bottom-up 방식이다.
개별 종목의 팩터를 계산해 랭킹을 내지만, 시장 전체의 거시 환경(금리·경기·인플레이션·버블 여부 등)을 고려하지 않는다.

이는 시장이 위험 구간에 있는 상황에서도 동일한 공격적 스크리닝을 실행하는 문제를 낳는다.

Bridgewater, BlackRock, AQR 등 실무 기관들은 공통적으로 Growth × Inflation 두 축으로 거시 국면을 분류하고, 이를 기반으로 팩터 비중과 포트폴리오 포지셔닝을 동적으로 조정한다.

## Decision

Croesus에 3-Layer Macro Score Engine을 추가한다.

- **Layer 1 (Regime)**: Growth 방향 × Inflation 방향으로 4개 국면 분류.
- **Layer 2 (Risk Amplifier)**: 유동성·신용·금리 지표로 국면 내 강도 조정.
- **Layer 3 (Confirmation)**: 변동성·추세·심리·FX 지표로 신호 확인 또는 경고.

이 3개 레이어의 출력인 `MacroState`는 Screening Engine의 파라미터 조정에만 사용된다. Macro 모듈은 스크리닝을 알지 못한다 (단방향 의존).

## Rationale

- 시장 전체 상황을 무시하면 개별 종목이 아무리 좋아도 진입 타이밍 리스크가 남는다.
- Growth-Inflation 프레임은 실증적으로 검증된 기관 투자 방법론이다.
- LLM 없이 수치 지표의 백분위수 기반 규칙만으로 구현 가능하다.
- FRED API와 yfinance로 대부분의 지표를 무료로 수집할 수 있다.
- Macro 모듈을 분리하면 향후 뉴스 LLM 분석(BlackRock MLP 방식)으로 확장할 수 있다.

## Alternatives Considered

### 단일 Macro Score

모든 지표를 가중 평균 하나로 합산하는 방식.
"왜 위험한가"를 설명하지 못해 리서치 참고 자료로서 가치가 낮다.

### 신호등 체계 (Green/Yellow/Red)

이분법 분류라 경계선 처리가 어색하고 정도(degree)를 잃는다.

## Consequences

### Positive

- 시장 과열·위기 시 자동으로 보수적 스크리닝으로 전환.
- 국면별로 "왜 이 포지셔닝인가"를 수치로 설명 가능.
- macro 모듈이 분리되어 있어 향후 LLM 레이어 추가 용이.

### Negative

- 지표 가중치와 임계값이 초기에는 경험적 추정값이다. 실증 검증 전까지 조정이 필요할 수 있다.
- AAII·NAAIM 스크래핑은 소스 구조 변경 시 깨질 수 있다.
- 월간·분기 지표는 후행성이 있어 최신 시장 변화를 즉시 반영하지 못한다.

## Follow-Up Decisions

- Amplifier Score 범주 가중치 실증 검증 방법 결정.
- 뉴스 LLM 분석으로 Sentiment 범주를 보완하는 시점 결정.
- 미국 외 글로벌 macro 지표 추가 시점 결정.
