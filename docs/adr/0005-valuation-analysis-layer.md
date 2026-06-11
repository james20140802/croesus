# ADR 0005: Add Valuation Analysis Layer

## Status

Accepted

## Context

Croesus의 기존 Factor Engine은 가격 데이터 기반 기술적 팩터(모멘텀, 변동성, 유동성, 이평선)만 계산한다.
개별 종목의 펀더멘털 가치를 평가하는 수단이 없어, 스크리닝 결과가 "움직임이 좋은 종목"에 편향될 수 있다.

투자의 핵심 질문 두 가지가 답해지지 않는 상태다:
1. **상대 가치**: 이 종목이 같은 업종의 다른 종목보다 비싼가, 싼가?
2. **절대 가치**: 현재 주가가 이 기업의 내재 가치 대비 어느 수준인가?

## Decision

Croesus에 Valuation Analysis Layer를 추가한다.

두 가지 출력 경로로 나눈다:

1. **스크리닝용 스칼라 팩터** → 기존 `factor_values` 테이블 (스키마 변경 없음)
   - 상대 가치: `pe_ratio`, `pb_ratio`, `ev_to_ebitda`, `fcf_yield`, 섹터 내 백분위 지표 3종
   - 절대 가치: `price_to_intrinsic` (현재가 / DCF 내재 가치)

2. **DCF 상세 기록** → 신규 `valuation_snapshots` 테이블
   - `intrinsic_value_per_share`, `upside_pct`, `wacc`, `fcf_growth_rate`, `assumptions_json`

재무제표 원본은 신규 `fundamentals` 테이블(롱포맷)에 저장한다.

데이터 소스는 `FundamentalsProvider` 인터페이스 뒤에 추상화한다. MVP는 yfinance, 이후 교체 가능.

DCF 가정값(WACC, 성장률)은 코드가 자동 계산(CAPM + 과거 FCF CAGR)하되, `overrides: dict` 파라미터를 통해 LLM이 나중에 주입할 수 있도록 설계한다.

## Rationale

- 기술적 팩터만으로는 "좋은 비즈니스를 싸게 사는" 원칙을 구현할 수 없다.
- `factor_values` 롱포맷은 새 팩터를 스키마 변경 없이 행 추가로 흡수한다.
  ~~Screening Engine 변경 불필요.~~ **(2026-06-11 정정, Sprint 008b)** 이 가정은
  틀렸다: 스크리닝 엔진은 등록된 팩터 이름만 로드하고 점수식도 차원별로
  명시적이므로, 새 팩터는 `croesus/screening/dimensions.py`에 이름·방향을
  등록하고 점수 차원에 연결해야 실제로 소비된다. 저장 스키마가 무변경인 것은
  맞지만 "자동 통합"은 아니다.
- DCF 결과를 `valuation_snapshots`에 별도 저장하면 LLM이 나중에 "왜 이 종목이 저평가인가"를 구조화된 데이터로 설명할 수 있다.
- `FundamentalsProvider` 추상화는 Valuation sprint에서 yfinance를 쓰고, 이후 FMP 등 유료 소스로 교체할 때 다운스트림 코드를 건드리지 않는다.
- WACC의 `overrides` 파라미터는 구현 비용 없이 LLM 확장 포인트를 열어둔다.

## Alternatives Considered

### factor_values 단일 테이블에 DCF 결과까지 저장

DCF는 float 하나(내재 가치)로 표현하기에 정보가 너무 많다. WACC, 성장률 가정, 시나리오 등 메타데이터를 저장할 공간이 없어 LLM 확장 시 막힌다.

### 별도 Valuation 모듈 (Factor Engine과 완전 분리)

스크리닝 엔진이 `factor_values`와 별개 경로로 밸류에이션 결과를 읽어야 해 스크리닝 파이프라인이 복잡해진다.

## Consequences

### Positive

- 가격 기반 기술적 팩터에 펀더멘털 밸류에이션이 추가되어 스크리닝 품질 향상.
- ~~Screening Engine 변경 없이 밸류에이션 팩터가 자동 통합됨.~~
  **(2026-06-11 정정, Sprint 008b)** 자동 통합되지 않았다. Sprint 008b가
  `dimensions.py` 팩터 등록 + `valuation_score` 차원 + `valuation` 가중치로
  명시적으로 통합했다. 단, 저장 측 이점(스키마 무변경, LLM이 읽을 구조화
  데이터)은 그대로 유효하다.
- DCF 기록이 구조화되어 Research Agent가 "왜 저평가인가"를 설명하는 데 사용 가능.
- `FundamentalsProvider` 추상화로 데이터 소스 교체 비용 최소화.

### Negative

- 분기 1회 재무제표 수집 잡(`quarterly_run`) 추가로 운영 복잡도 소폭 증가.
- yfinance 재무제표 데이터는 정확도·완결성이 제한적. 오류 처리가 중요.
- 신규 상장 종목 등 재무 데이터가 부족한 경우 DCF 스킵 처리 필요.

## Follow-Up Decisions

- yfinance 재무제표 데이터 신뢰도 평가 후 FMP 전환 시점 결정.
- DCF LLM 오버라이드 기능 구현 시점 결정.
- Quality/Growth/Leverage 팩터 추가 스프린트 결정.
