# 사용자 관점 요구사항 반영 현황 보고서

작성일: 2026-06-04

## 목적

이 문서는 Croesus가 사용자가 기대하는 개인 포트폴리오 관리 제품으로 가고 있는지, 현재 구현과 계획을 사용자 관점에서 점검한다. 내부 모듈 구조보다 사용자가 실제로 경험하는 흐름을 기준으로 정리한다.

Croesus는 당장은 CLI로 실행되는 로컬 프로그램이지만, 최종 사용 경험은 로컬 웹/앱으로 확장될 수 있어야 한다. 따라서 CLI command는 최종 제품 형태가 아니라 초기 조작면이다. 핵심 로직은 나중에 로컬 UI, API, scheduler가 재사용할 수 있는 구조로 유지해야 한다.

목표 흐름은 다음과 같다.

```text
투자 성향 설정
  -> 목표 포트폴리오 설정
  -> 현재 포트폴리오 입력 및 추적
  -> 시장 / 종목 / 포트폴리오 분석
  -> 리밸런싱 제안
  -> 사용자 승인
```

핵심 원칙은 두 가지다.

- 계산 가능한 값은 코드가 계산한다: 가격, 수익률, 변동성, 비중, 노출, 밸류에이션, 리밸런싱 조건.
- LLM은 정성적 해석에만 쓴다: 뉴스, 공시, 실적 발표, 경쟁 구도, 규제 리스크, 경영진 코멘트.

## 요약 판단

| 사용자 요구사항 | 현재 상태 | 판단 |
|---|---:|---|
| 투자 성향을 쉽게 정할 수 있는가 | 부분 구현 | guided onboarding과 3개 policy template은 있음. 이름과 UX는 더 사용자 친화적으로 다듬어야 함 |
| 포트폴리오를 계속 추적하는가 | 부분 구현 | 가격, FX, market value, exposure, policy drift는 계산함. 지속 추적/히스토리/스케줄러는 계획 단계 |
| 종목과 거래 변화를 쉽게 입력할 수 있는가 | 계획됨 | 현재는 `asset_id` 기반 CSV. symbol resolver와 transaction ledger가 계획되어 있음 |
| 시장 분석이 투자 방향 결정에 도움을 주는가 | 구현됨, 통합은 진행 중 | MacroState와 macro report는 있음. 실제 리밸런싱 반영은 Sprint 006 계획 |
| 주목 종목을 찾아 포트폴리오 반영을 돕는가 | 계획됨 | factor 기반 screening, sector/theme 분석, research agent, candidate add가 계획됨 |
| 목표 수익률 달성을 보장/관리하는가 | 부분 계획 | 수익률 달성 보장은 불가능. 대신 목표와 리스크의 현실성 검증, 추적, 리밸런싱 제안을 해야 함 |
| CLI 이후 웹/앱 확장을 고려하는가 | 부분 계획 | local scheduler/freshness는 계획됨. 다만 app-ready API/use-case boundary를 더 명시해야 함 |

현재 제품은 "데이터 기반 포트폴리오 진단기"에 가까운 상태다. 목표 상태는 "내 프로필에 맞춰 포트폴리오를 계속 감시하고, 필요한 경우 근거 있는 행동을 제안하는 로컬 포트폴리오 운영체제"다.

## CLI-first, App-ready 원칙

Croesus는 개인 로컬 환경에서 돌아가는 도구이므로 초기에는 CLI가 합리적이다. 하지만 최종 사용 경험이 웹/앱으로 확장될 수 있으려면 CLI 중심으로만 설계하면 안 된다.

### 제품 방향

최종 형태는 클라우드 SaaS가 아니라 다음에 가깝다.

```text
로컬 데이터베이스 DuckDB
  + 로컬 백엔드 API / service layer
  + 로컬 scheduler
  + 로컬 웹 UI 또는 desktop app
```

사용자는 나중에 다음 화면을 기대할 수 있다.

- 투자 성향 설정 wizard
- portfolio dashboard
- holdings/transaction 입력 화면
- 데이터 freshness 상태
- macro report 화면
- candidate/watchlist 화면
- rebalance proposal review 화면
- 승인/기록 화면

### 구현 기준

앞으로 기능을 만들 때 다음 기준을 지켜야 한다.

- CLI command 안에 business logic을 넣지 않는다.
- CLI는 `run_*` use-case 함수를 호출하는 얇은 entrypoint여야 한다.
- 모든 주요 결과는 사람이 읽는 report보다 먼저 structured data로 저장한다.
- UI는 DB table과 use-case result를 읽어 화면화할 수 있어야 한다.
- 사용자 승인 gate는 CLI에서도 UI에서도 동일한 모델을 써야 한다.
- scheduler/freshness는 화면에서 바로 표시할 수 있는 상태 데이터로 남겨야 한다.
- report text는 최종 산출물이지만, report만 저장하고 structured action을 버리면 안 된다.

권장 계층:

```text
data_sources/
  외부 데이터 수집

repositories/
  DuckDB 읽기/쓰기

domain services/
  profile validation, mark-to-market, exposure, screening, rebalancing

jobs/
  CLI에서 호출하는 orchestration

local api / ui later
  같은 domain service와 repository를 재사용
```

즉, CLI는 첫 번째 interface일 뿐이고, 나중에 웹/앱은 같은 기능을 다른 interface로 제공해야 한다.

## 1. 투자 성향을 쉽게 정할 수 있는가

### 요구사항

사용자는 처음부터 복잡한 자산배분표를 직접 설계하고 싶지 않다. 다음 정도를 쉽게 선택하거나 안내받을 수 있어야 한다.

- 기본형
- 공격형
- 방어형
- 기대수익률
- 허용 가능한 손실
- 투자 기간
- 현금 필요성
- 거래 자동화 수준

### 현재 반영 상태

부분적으로 구현되어 있다.

현재 `profile_init`은 다음 흐름을 제공한다.

- 기본 profile seed
- YAML config 생성 및 로드
- interactive profile 입력
- guided profile 입력
- guided 입력 후 policy template 추천

현재 코드에 있는 template은 다음 3개다.

| 사용자 친화적 이름 | 현재 코드 이름 | 의미 |
|---|---|---|
| 공격형 | `growth_long_term` | 긴 투자 기간, 높은 기대수익률, 큰 drawdown 허용 |
| 기본형 | `balanced_long_term` | 중간 수준의 기대수익률, 기간, 손실 허용 |
| 방어형 | `capital_preservation` | 낮은 손실 허용, 짧은 기간, 큰 유동성 필요 |

### 부족한 점

사용자 관점에서는 아직 `default`, `aggressive`, `defensive` 중에서 고르는 것처럼 보이지 않는다. 내부 template 이름도 제품 언어와 다르다.

필요한 개선:

- `default`, `aggressive`, `defensive` alias 제공
- "나는 공격형/방어형이다" 식의 빠른 선택 모드 제공
- 선택 후 기대수익률, 허용손실, 현금비중을 조정하는 짧은 wizard 제공
- UI 확장을 위해 profile creation 결과를 structured summary로 반환

## 2. 내 포트폴리오를 계속 추적하는가

### 현재 반영 상태

부분적으로 구현되어 있다.

현재 가능한 것:

- yfinance 가격 수집
- FX rate 수집
- holdings CSV import
- 저장된 가격과 FX 기반 mark-to-market
- 총 market value 계산
- cost basis와 unrealized P&L 계산
- position/sector/industry/theme/country/currency exposure 계산
- profile limit 위반 여부 표시
- policy target 대비 drift 계산

즉, "현재 상태를 계산하는 snapshot"은 구현되어 있다.

### 계획된 것

지속 추적을 위해 다음이 계획되어 있다.

- Sprint 006b: local scheduler와 data freshness
- Sprint 006c: transaction ledger
- Sprint 007: fundamentals와 valuation 분석

이 세 가지가 붙어야 사용자가 기대하는 "계속 추적한다"는 느낌이 완성된다.

### App-ready 보완

웹/앱으로 확장하려면 snapshot 계산 결과를 report 문장으로만 남기면 안 된다. dashboard가 바로 읽을 수 있도록 다음 상태가 구조화되어야 한다.

- latest portfolio value
- daily/monthly return
- contribution-adjusted return
- exposure violations
- policy drift status
- stale data domains
- pending proposed actions
- latest warnings

권장 사용자 출력:

```text
포트폴리오 상태: 주의
- 총 평가금액: $102,400
- 월간 변화: +2.1%
- 신규 입금 제외 투자수익률: +0.8%
- NVDA 비중: 13.8% (허용 10.0% 초과)
- Technology 비중: 41.2% (허용 35.0% 초과)
- Cash 비중: 3.1% (최소 5.0% 미달)
- 가격 데이터: 최신
- 매크로 데이터: 2일 전
```

## 3. 내 종목과 포트폴리오 변화를 쉽게 입력할 수 있는가

### 현재 반영 상태

현재는 제한적이다.

현재 holdings import는 `asset_id` 중심이다. 예를 들어 `US_EQ_AAPL` 같은 내부 ID를 알아야 한다. `CASH_USD` 같은 cash row는 지원한다.

### 계획된 것

Sprint 004c에서 holdings onboarding과 asset resolver가 계획되어 있다.

계획된 사용자 경험:

```csv
portfolio_id,symbol,asset_id,quantity,avg_cost,currency,market_value
default,AAPL,,10,150,USD,
default,VOO,,5,430,USD,
default,,CASH_KRW,,,KRW,421391
```

Sprint 006c에서는 transaction ledger가 계획되어 있다.

계획된 transaction type:

- `buy`
- `sell`
- `deposit`
- `withdrawal`
- `dividend`
- `fee`
- `manual_adjustment`

### App-ready 보완

웹/앱에서는 CSV가 유일한 입력이면 안 된다. CSV는 bulk import와 reconciliation 용도로 유지하되, 일반 사용자는 form으로 다음을 입력할 수 있어야 한다.

- ticker
- 거래 유형
- 수량
- 가격
- 수수료
- 통화
- 거래일
- 연결된 proposal action

따라서 transaction ledger는 UI 확장의 핵심 기반이다. holdings snapshot은 현재 상태를 빠르게 bootstrap하는 도구이고, 장기적으로는 transactions에서 holdings를 derivation하는 방향이 맞다.

## 4. 시장 분석을 통해 투자 방향 결정에 도움을 주는가

### 현재 반영 상태

MacroState 계산은 구현되어 있다.

현재 macro layer는 다음을 계산한다.

- Growth direction
- Inflation direction
- Regime: Goldilocks, Reflation, Stagflation, Deflation
- Regime confidence
- Risk amplifier score
- Confirmation score
- Positioning: Aggressive, Moderately Aggressive, Neutral, Cautious, Defensive
- warnings
- opportunities
- macro-adjusted screening params

Macro report도 Markdown/CSV로 생성된다.

### 계획된 통합

Sprint 005에서는 MacroState가 factor weight, filter, candidate count에 반영된다.

Sprint 006에서는 MacroState가 리밸런싱 제약으로 반영된다.

예상 규칙:

| Macro positioning | 투자 방향 |
|---|---|
| Aggressive | profile과 drift가 허용하면 add 가능 |
| Moderately Aggressive | 정상 risk budget 허용 |
| Neutral | policy drift 중심으로만 조정 |
| Cautious | 신규 satellite add 제한 |
| Defensive | trim, cash 회복, defensive sleeve 우선 |

### App-ready 보완

Macro report는 문서뿐 아니라 dashboard card로 표현되어야 한다.

필요한 structured fields:

- current regime
- positioning
- confidence
- top warnings
- top opportunities
- applied screening adjustments
- portfolio-level implication

예시:

```text
시장 판단: Cautious

포트폴리오 적용:
- 신규 위성주식 매수는 제한합니다.
- 이미 초과한 Technology 노출을 더 늘리는 후보는 제외합니다.
- 현금 비중이 최소치보다 낮으면 cash 회복을 우선합니다.
```

## 5. 주목해야 할 종목을 찾아서 포트폴리오 반영을 돕는가

### 현재 반영 상태

아직 실제 screening module은 구현되어 있지 않다. `factor_values`와 macro screening params는 준비되어 있지만, `screening_results`에 후보 ranking을 만드는 코드는 아직 없다.

### 계획된 것

Sprint 005에서 screening이 계획되어 있다.

계획된 score:

```text
score =
  weight(momentum) * momentum_score
+ weight(liquidity) * liquidity_score
+ weight(trend) * trend_score
- weight(volatility_penalty) * volatility_penalty
```

Sprint 007에서는 valuation factor가 추가된다.

계획된 valuation factor:

- `pe_ratio`
- `pb_ratio`
- `ev_to_ebitda`
- `fcf_yield`
- `pe_vs_sector_pct`
- `pb_vs_sector_pct`
- `ev_ebitda_vs_sector_pct`
- `price_to_intrinsic`

Sprint 008에서는 Research Agent가 shortlisted candidates에만 붙는다.

### 사용자 출력 기준

좋은 종목과 내 포트폴리오에 지금 사도 되는 종목은 다르다. 따라서 후보 report는 반드시 portfolio fit을 같이 보여줘야 한다.

```text
주목 후보

1. VOO
   - 후보 이유: core_us_equity sleeve가 부족하고 broad market ETF로 분산 효과가 큼
   - 포트폴리오 제약: Technology concentration을 악화하지 않음
   - 제안: add 후보

2. AAPL
   - 후보 이유: trend와 liquidity 양호
   - 제한 이유: Technology sector가 이미 max를 초과
   - 제안: watch, 신규 매수 보류
```

## 6. 결국 내가 목표로 하는 수익률을 달성할 수 있는가

목표 수익률 달성은 보장할 수 없다. 어떤 시스템도 시장 수익률, 미래 가격, 경기 침체, 개별 기업 리스크를 확정적으로 통제할 수 없다.

Croesus가 해야 하는 일은 수익률 보장이 아니라 다음이다.

- 목표 수익률과 허용 손실이 현실적인 조합인지 검증
- 현재 포트폴리오가 목표 수익률을 추구하기에 너무 방어적인지 판단
- 현재 포트폴리오가 허용 손실 대비 너무 공격적인지 판단
- 장기 목표 대비 진행률을 추적
- 과도한 집중, 과도한 turnover, macro risk를 줄임
- 기대수익률을 높일 수 있는 후보를 찾되, profile 제약 안에서만 제안

권장 사용자 출력:

```text
목표 수익률 점검

목표: 연 10%
현재 6개월 연환산 수익률: 7.8%
현재 risk 상태: 허용 범위 초과

판단:
- 목표 수익률을 위해 주식 비중은 필요하지만,
  현재 수익 추구가 Technology/NVDA 집중으로 과도하게 몰려 있습니다.
- 신규 고위험 종목을 추가하기보다 core equity와 cash balance를 회복하는 것이 먼저입니다.
```

## 투자 결정은 구체적으로 어떻게 해야 하는가

아직 최종 전략은 완전히 구현되어 있지 않다. 하지만 현재 문서와 코드 방향상 가장 자연스러운 전략은 `profile-first core-satellite rebalancing`이다.

## 추천 전략 A: Profile-first Core-Satellite Rebalancing

가장 추천하는 기본 전략이다.

포트폴리오를 네 개 sleeve로 나눈다.

| Sleeve | 역할 |
|---|---|
| Core US Equity | 장기 시장 수익률을 받는 중심축 |
| Satellite Equity | 초과수익을 노리는 제한된 고확신 후보 |
| Defensive Bonds | drawdown 완화와 방어 |
| Cash | 유동성, 기회 대기, 강제 매도 방지 |

의사결정 순서:

1. Profile이 유효한지 확인한다.
2. 현재 포트폴리오의 총액, 현금, 종목 비중, 섹터/산업/테마/국가/통화 노출을 계산한다.
3. 단일종목, 섹터, 산업, 테마, 국가, 통화 limit 위반을 먼저 찾는다.
4. Policy sleeve가 min/max band 밖인지 확인한다.
5. MacroState로 risk posture를 정한다.
6. Factor screening으로 후보를 찾는다.
7. 후보가 현재 포트폴리오의 과잉 노출을 악화하는지 확인한다.
8. Valuation이 너무 비싼 후보는 add 대신 watch로 보낸다.
9. 필요한 후보만 qualitative research를 붙인다.
10. Turnover limit 안에서 action을 만든다.
11. 사용자에게 report를 보여주고 승인 전에는 실행하지 않는다.

## 추천 전략 B: Goal-Gap Rebalancing

목표 수익률 달성 가능성에 더 직접적으로 초점을 맞추는 전략이다.

```text
goal_gap = target_return - portfolio_expected_return_proxy
```

gap이 크면 더 성장형 allocation을 검토하고, gap이 작거나 risk가 초과되면 방어적으로 조정한다.

이 전략은 사용자가 묻는 "내 목표 수익률에 도달하고 있나?"에 직접 답하기 좋다. 다만 expected return proxy가 불확실하므로 단독 매수/매도 신호로 쓰면 안 된다.

## 추천 전략 C: Macro-Aware Risk Budget

MacroState를 독립 매수/매도 신호로 쓰지 않고, profile이 허용하는 범위 안에서 risk budget만 조정한다.

```text
Aggressive:
  candidate_count 확대
  satellite add 허용

Neutral:
  policy drift 중심

Cautious:
  신규 고위험 add 제한
  liquidity/volatility filter 강화

Defensive:
  trim, cash, defensive sleeve 우선
```

## 최종 추천 전략

Croesus의 기본 전략은 다음 조합이 가장 적절하다.

```text
Profile-first Core-Satellite Rebalancing
  + Goal-Gap Progress Check
  + Macro-Aware Risk Budget
```

실제 투자 결정은 다음 우선순위를 따라야 한다.

1. Profile이 유효하지 않으면 아무 action도 제안하지 않는다.
2. 단일종목/섹터/현금/통화 등 명확한 risk violation을 먼저 해결한다.
3. Policy band 밖에 있는 sleeve를 target 쪽으로 되돌린다.
4. Macro가 Cautious/Defensive이면 신규 risk 추가를 제한한다.
5. Screening 후보는 마지막에 고려한다.
6. 후보가 포트폴리오 제약을 악화하면 매수하지 않는다.
7. Valuation이 비싸거나 정성 리스크가 크면 watch로 둔다.
8. Turnover limit을 넘지 않는다.
9. 보고서로 제안하고, 사용자가 승인하기 전에는 실행하지 않는다.

## 웹/앱 확장을 고려한 다음 작업 권장 순서

### 1순위: use-case boundary 정리

각 job은 CLI용 구현체가 아니라 UI에서도 호출 가능한 use-case 함수여야 한다.

완료 기준:

- `run_profile_init`
- `run_portfolio_snapshot`
- `run_screening_job`
- `run_rebalance_check`
- `run_local_sync`
- `record_transaction`

각 함수가 structured result를 반환한다.

### 2순위: profile template UX 정리

현재 구현된 template을 사용자 언어로 노출한다.

- `default` -> `balanced_long_term`
- `aggressive` -> `growth_long_term`
- `defensive` -> `capital_preservation`

### 3순위: holdings 입력 UX 개선

Sprint 004c를 구현한다. 사용자가 `AAPL`, `VOO`처럼 symbol만 넣어도 되어야 한다.

### 4순위: transaction ledger

웹/앱으로 가려면 transaction ledger는 핵심이다. 매수/매도/입금/배당을 form으로 기록하고, holdings는 그 결과로 계산되어야 한다.

### 5순위: freshness와 local scheduler

dashboard는 "지금 데이터가 믿을 만한가"를 보여줘야 한다. 가격, FX, macro, portfolio, screening, report freshness를 분리해서 저장해야 한다.

### 6순위: screening과 rebalance report

사용자가 "그래서 뭘 해야 하지?"에 대한 답을 받는 핵심 기능이다. 결과는 Markdown report뿐 아니라 structured action rows로 저장되어야 한다.

## 결론

사용자가 말한 요구사항은 큰 방향에서는 대부분 반영되어 있다. 다만 현재 구현 상태는 다음처럼 나뉜다.

- 이미 구현: profile/guided template, 가격 수집, FX, factor 계산, macro analysis, portfolio snapshot, exposure, policy drift
- 계획됨: symbol 기반 holdings 입력, transaction ledger, 지속 freshness 추적, screening, rebalance proposal, valuation, research agent
- 추가로 명시해야 할 방향: CLI는 최종 제품이 아니라 초기 interface이며, 모든 핵심 로직은 로컬 웹/앱에서 재사용 가능한 use-case와 structured data 중심으로 유지해야 한다

가장 중요한 제품 판단은 이것이다.

Croesus는 "좋은 종목을 맞히는 도구"가 아니라 "내 투자 목표와 위험 한도 안에서 포트폴리오를 계속 운영하도록 돕는 도구"여야 한다. 따라서 추천 전략은 개별 종목 pick보다 profile, policy, risk violation, macro posture, candidate fit 순서로 투자 결정을 내리는 방식이 맞다.
