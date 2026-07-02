# 시장 시그널 연구 로드맵 (Research Backlog)

> **이 문서의 용도.** 다음에 해볼 만한 실험들을 각각 *cold-start 가능한 미니 스펙*으로 정리한다.
> 새 세션에서 "로드맵 ①번 실험 해줘"라고 하면, 이 문서만 읽고 바로 구현을 시작할 수 있게 하는 것이 목표다.
> 한 번에 하나씩 구현 → 리포트 → compact → 다음 실험. 각 실험은 `experiments/market_signals/`와
> 같은 자립형 모듈 + `FINDINGS.md` + (원하면) PDF 리포트를 산출한다.

**관련 문서:** 1차 실험(TimesFM / 푸리에 / 이벤트) 설계·결과는
`docs/superpowers/specs/2026-06-30-market-signal-experiments-design.md`,
`experiments/market_signals/report/report.pdf` 참조.

---

## 왜 이 실험들인가 (1차 실험의 교훈)

1차 실험 3개는 전부 같은 *가장 어려운 케이스*를 건드렸다 — **지수 전체의 절대 방향을, 저빈도로, 무조건적으로** 예측.
이건 시장에서 가장 효율적이고 가장 많이 차익거래된 구석이라 null이 나오는 게 정상이다. 엣지는 **구조가 남아있는 곳**에 있다:

| 축 | 1차 실험(어려움) | 2차 방향(유망) |
|----|-----------------|----------------|
| 절대 → **상대** | 지수 방향 | 종목 간 cross-sectional (시장 드리프트 상쇄) |
| 무조건 → **조건부** | 항상 같은 신호 | 레짐(성장×인플레) 조건부 |
| 1차 → **2차 모멘트** | 수익률(예측 불가) | 변동성(예측 가능, vol clustering) |
| 거시 이벤트 n=4 → **종목 이벤트 n=수백** | 검정력 없음 | 통계적 검정력 확보 |

---

## 우선순위 & 상태

| # | 실험 | 왜 유망 | 신규 데이터 필요 | 규모 | 상태 |
|---|------|---------|------------------|------|------|
| ① | 우리 신호 검증 (cross-sectional IC) | 파이프라인 핵심 베팅을 직접 검증, 상대라 드리프트 상쇄 | 없음 | 중 | **DONE** (2026-07-01) |
| ③ | 종목 이벤트 스터디 (PEAD류) | 이벤트 드리븐이 Croesus 본 thesis, 큰 표본 | (컨센서스 없음 → 프록시) | 중~대 | **DONE** (2026-07-02) |
| ② | 변동성 예측 + 리스크 타게팅 | vol은 확실히 예측 가능, null→positive | 없음 | 중 | **DONE** (2026-07-02) |
| ④ | 레짐 조건부 팩터 성과 | 조건부 신호는 무조건이 못 사는 곳에 산다 | 없음 | 중 | **DONE** (2026-07-03) |
| ⑤ | LLM 알파 감사 (보너스) | 비싸고 핵심적인 LLM 베팅 검증 | 없음 | 소 | TODO |

권장 순서: **① → ③ → ② → ④** (⑤는 언제든 저비용으로).

> **① 결과 요약(2026-07-01, PR #58).** `experiments/market_signals/cross_sectional/`
> (플랜: `docs/superpowers/plans/2026-07-01-cross-sectional-ic.md`, 결론: `.../cross_sectional/FINDINGS.md`).
> - **데이터 갭 발견**: `factor_values`는 역사화 안 됨(2026-05-27 이후 ~5주뿐) → fundamental/valuation/
>   DCF/LLM 신호는 **지금 과거검증 불가**. 그래서 ①은 `prices_daily` 전 히스토리에서 재계산한
>   **가격 팩터 7종**만 검증(2016~2026, 521종목, 121개 월별 cross-section).
> - **결론**: 대부분 예측력 약함. `beta_1y`·`volatility_3m`(순위상관 0.64, 사실상 동일 "고위험" 베팅)은
>   Sharpe≈0.8지만 survivorship+단일강세장 아티팩트 → BAB와 반대, alpha 아님. **유일하게 깨끗한
>   신호는 `momentum_6m`**(IC t≈2.1, MaxDD −9%, beta/vol과 직교, 20bps 후 Sharpe 0.74). 경계선.
> - **④로 연결**: `results/cross_sectional/perdate_momentum_6m_{63,126}.csv` 롱숏 시계열을 레짐별 분해에 재사용.
> - **③/⑤ 함의**: fundamental·LLM 신호 IC 검증은 `factor_values`/`thesis_grades` 역사화(시점별 스냅샷 축적)
>   가 선행돼야 함 — 현재는 순방향 1개월치뿐. ⑤는 등급 스냅샷이 쌓일 때까지 표본이 매우 작다.

> **② 결과 요약(2026-07-02).** `experiments/market_signals/vol_targeting/`
> (플랜: `docs/superpowers/plans/2026-07-02-vol-targeting.md`, 결론: `.../vol_targeting/FINDINGS.md`).
> - **두 가설 모두 확인**: (a) EWMA/GARCH(1,1)가 naive를 QLIKE에서 유의하게 이김(DM t≈−3.9~−4.2,
>   SPY 1993~·EW 유니버스 1990~, 월별 walk-forward 364~376개월). (b) vol-targeting(σ_target 0.15,
>   cap 1.0, 10bps 비용)이 **MaxDD를 SPY −55%→−40%, EW −51%→−28%로 감소**, Sharpe +0.07~0.15.
> - **정직한 관찰**: 오버레이 성과는 naive≈ewma≈garch — 효과 대부분이 "최근 vol로 스케일" 규칙
>   자체에서 나옴. **risk-gate에는 단순 규칙으로 충분**, GARCH 인프라 불요. oracle 격차(Sharpe +0.4)는
>   예측 개선 여지.
> - `macro_scores.amplifier_score` 비교는 **불가**(14일치만 존재 — factor_values와 같은 역사화 갭).
> - ①(cross-sectional 1차 모멘트 null)과 대조: 시계열 2차 모멘트는 positive. 엣지는 vol clustering에 있었다.

> **③ 결과 요약(2026-07-02).** `experiments/market_signals/event_drift/`
> (플랜: `docs/superpowers/plans/2026-07-02-event-drift.md`, 결론: `.../event_drift/FINDINGS.md`).
> - **3b 변형**: 프로덕션 `events`는 5일치·`disclosures`는 0행(역사화 갭)이라 원문 3b 그대로는 불가 →
>   `croesus/events/detectors.py`의 규칙(3σ return, z≥2 volume; trailing-only라 look-ahead 없음)을
>   30년·521종목 이력에 소급 재계산. 이벤트 n=14.6만(dedup 후) — 1차의 n=3~4 검정력 문제 해소.
> - **가격 급변(±3σ) 이벤트: drift 없음** (up/down 모두, 시장조정·placebo·날짜군집 NW 통제) —
>   ①의 "가격 신호 null"을 이벤트 차원에서 재확인.
> - **거래량 급증(z≥2) 이벤트: 유의한 양의 drift** (h=1~60 전 구간 t≈3~5, placebo null) — 그러나
>   크기가 작고(21일 +0.23%) 서프라이즈 크기와 단조 관계 없음, **10bps 비용 후 전멸**(Sharpe
>   0.47~0.74 → 전부 음수). "통계적 아노말리 ≠ tradable alpha"의 교과서적 사례.
> - **함의**: abnormal_return은 조사 트리거로만, abnormal_volume은 확인 필터 후보로만. 진짜 PEAD는
>   3c(컨센서스 수집) 선행 필요 — 별도 데이터 태스크로 남김.

> **④ 결과 요약(2026-07-03).** `experiments/market_signals/regime_conditional/`
> (플랜: `docs/superpowers/plans/2026-07-02-regime-conditional.md`, 결론: `.../regime_conditional/FINDINGS.md`).
> - **레짐 라벨 소급 재계산**: `macro_scores`는 14일치(역사화 갭) → 프로덕션 투표 함수를 FRED
>   point-in-time 뷰(발표 시차 적용)로 1990~2026 월별 소급 실행. 소급 라벨이 프로덕션 14일치와 일치.
> - **핵심 발견 = 프로덕션 버그**: 인플레 투표가 CPI/PCE/임금 *레벨* 기울기 사용 → 98~99% "Rising"
>   퇴화 → 프로덕션 레짐의 70%가 Reflation(Goldilocks 437개월 중 4개월). **YoY 변환 필요** — 별도
>   이슈 보고 가치. YoY 보정 라벨은 균형 분포(35/36/13/16%)이나 지속성 1.4~2.1개월로 짧음.
> - **팩터 조건부**: momentum_6m만 두 라벨 변형에서 일관 — Goldilocks 강세(+0.6~1.5%/월) /
>   Deflation·Stagflation 붕괴(−1.2~−2.0%/월, momentum crash와 부합), shift placebo p=0.077/0.044
>   (14회 검정 중 2개 유의 — 경계선). 레짐은 시장 자체를 예측 못 함(p=0.6~0.8).
> - **함의**: screening 가중치 레짐 조정은 근거 부족으로 보류 권장(라벨 지속성 짧아 실행 시점엔
>   국면이 지나감). 드로다운 통제는 ②의 vol-targeting이 더 싸고 견고. 인플레 투표 YoY 수정이 선행 과제.

---

## 공통 규약 (모든 실험 적용)

- **자립형 모듈**: `experiments/market_signals/<exp>/` 아래. 가격은 `experiments/market_signals/common/data.py`
  (DuckDB read-through, `prices_daily`) 재사용. 메인 `croesus/`는 **읽기만**, 통합 안 함.
- **DB 재사용**: `storage/croesus.duckdb`. 스키마는 `croesus/db/schema.sql`. 두 번째 DB 만들지 말 것.
- **TDD**: 순수 계산 모듈(IC, 이벤트 정렬, vol 모델, 회귀)은 test-first. 모델 추론·플롯은 smoke-run.
- **무거운 deps** 격리: 실험 전용 `requirements.txt`, 루트 `pyproject.toml` 손대지 말 것.
- **산출물**: `results/<exp>/`(gitignore) + `FINDINGS.md`(정직한 결론). 원하면 `report/`에 PDF.

### 자신을 속이지 않기 (1차 실험에서 실제로 나온 함정들)

- **다중검정**: 수백 개 종목/구간/신호를 동시에 보면 5%는 우연히 유의하다. 항상 우연 기대치를 병기하고,
  가능하면 Bonferroni/BH 보정 또는 out-of-sample 재확인. (푸리에 실험의 핵심 교훈.)
- **Look-ahead / point-in-time**: 시점 t의 예측에 t 이후 정보(수정된 fundamentals, 미래 리밸런스)를 쓰지 말 것.
  `factor_values`·`fundamentals`가 point-in-time인지 확인(수정 이력 없으면 근사).
- **Survivorship bias**: `assets` 테이블이 현재 상장 종목만 담고 있으면 상장폐지 종목이 빠져 결과가 낙관 편향된다.
  최소한 이 한계를 FINDINGS에 명시.
- **소표본**: 이벤트/레짐을 쪼개면 n이 급감. 신뢰구간을 반드시 보고하고 과대해석 금지. (이벤트 실험 교훈.)
- **거래비용**: 롱숏·리밸런싱 시뮬은 비용(bps) 민감도를 최소 한 번 보고. `croesus/backtest/engine.py`에 `--cost-bps` 있음.

---

## ① 우리 신호 검증 — Cross-sectional Information Coefficient

**한 줄.** Croesus가 이미 계산하는 신호들(팩터·DCF·LLM 등급)이 *실제로* forward 종목 수익률을 예측하는가?

**동기.** 파이프라인은 여러 신호를 계산해 종목을 고르지만, 각 신호가 예측력이 있는지 체계적으로 검증한 적이 없다.
지수 방향과 달리 **cross-sectional**(같은 날 종목 간 순위)은 시장 공통 드리프트가 상쇄돼 순수 신호가 드러난다.

**가설(반증가능).** 신호 $s$의 시점별 순위와 forward 수익률의 순위상관(Spearman IC)의 평균이 0보다 유의하게 크다.
동치로, 신호 상위분위(Q5) − 하위분위(Q1) 롱숏 스프레드가 양(+)이고 비용 차감 후에도 살아남는다.

**데이터 & 인프라 (실제 참조).**
- 신호 원천: `factor_values(asset_id, date, factor_name, value)` — 팩터명 `momentum_1m/3m/6m`, `volatility_3m`,
  `liquidity_1m`, `above_200d_ma`, `beta_1y`, `roe`, `net_margin`, `debt_to_equity`, `price_to_intrinsic`
  (`croesus/factors/common.py`, `croesus/factors/equity/`).
- 밸류 신호: `intrinsic_value_bands.upside_pct`(base 시나리오), 그리고 `normalized_dcf_snapshots.plausibility_gap`
  (**주의: 이 신호는 main의 PR #59에만 있고 이 worktree엔 없음 → 먼저 main rebase 필요**,
  `croesus/factors/equity/normalized.py`).
- LLM 신호: `thesis_grades.confidence`, `moat_grade` 등(→ 실험 ⑤와 겹침, 여기선 수치 등급만).
- forward 수익률: `prices_daily`에서 $h\in\{21,63,126\}$ 거래일 전방 수익률 계산.

**방법.**
1. 시점 그리드(월 1회 등)마다 종목 cross-section 구성: 각 종목의 신호값 + $h$일 forward 수익률.
2. 시점별 Spearman IC 계산 → 시계열 평균 IC, IC의 t-stat(Newey-West), IC IR(=mean/std).
3. 분위 포트폴리오: 신호로 5분위, Q5−Q1 롱숏 수익률 시계열 → 누적, Sharpe, 비용 민감도.
4. 신호 간 비교표 + IC decay(h에 따른 IC 감쇠) 곡선.

**기준선/통제.** 무작위 신호(순열 검정)로 IC=0 귀무 분포; 섹터 중립화(섹터 내 순위)로 섹터 베팅 제거.

**산출물.** 신호×horizon IC 표, IC decay 곡선, Q5−Q1 누적수익 곡선, FINDINGS(어떤 신호가 진짜 예측력 있나).

**성공 기준.** 최소 한 신호가 IC t-stat |t|>2, IC decay가 완만, 롱숏 스프레드가 합리적 비용 후 양(+).
정직한 "대부분 예측력 없음"도 유효한 결과(어떤 신호에 집중할지 알려줌).

**함정.** point-in-time 위반(수정 fundamentals), survivorship, 팩터 간 상관(중복), 소수 대형주 지배.

**규모.** 중. 순수 IC/분위 계산은 TDD로 명확. 데이터 조립이 대부분.

---

## ③ 종목 레벨 이벤트 스터디 (PEAD류)

**한 줄.** 종목 단위 사건 후 사후 표류(drift)가 존재하는가? 1차 이벤트 실험을 **큰 표본**으로 제대로.

**동기.** 1차 이벤트 실험은 거시 사건 n=3~4로 검정력이 없었다. 종목 사건은 수백 개라 통계가 산다.
Post-earnings-announcement drift(PEAD)는 가장 견고한 아노말리 중 하나이고, 이벤트 드리븐이 Croesus opportunity engine의 본 thesis다.

**⚠️ 데이터 갭(중요, 먼저 결정).** 어닝 **서프라이즈 = 실적 − 컨센서스**를 계산할 **애널리스트 컨센서스 데이터가 저장소에 없다**
(`fundamentals`는 actuals만: `revenue, eps, free_cash_flow`). 세 가지 경로:
- **(3a) 공시일 이벤트 + 실적변화 프록시** (신규 데이터 0): `disclosures`(SEC EDGAR: `form_type, filed_date, report_date`)의
  10-Q/10-K 제출일을 이벤트로, 서프라이즈 프록시는 `fundamentals`의 YoY/QoQ 실적 변화 부호·크기. 컨센서스 대비는 아니지만 즉시 가능.
- **(3b) 기존 `events` 테이블 후 표류** (신규 데이터 0): `events(asset_id, as_of_date, event_type, direction, magnitude)`의
  `abnormal_return`/`abnormal_volume` 이벤트 후 CAAR 표류 측정. event_scan이 이미 asset-level 이벤트 생성(`croesus/events/scan.py`).
- **(3c) 컨센서스 소스 추가** (선행 작업): yfinance/Finnhub 등에서 EPS estimate 수집 → 진짜 SUE 기반 PEAD. 별도 데이터 태스크로 분리.

→ **권장: 3b로 시작**(제로 데이터, 즉시 검증), 유망하면 3a, 진짜 PEAD 원하면 3c.

**가설.** 사건일 T=0 이후 [T+1, T+60]에서 사건 방향과 같은 부호의 유의한 누적초과수익(CAAR)이 존재한다(drift).

**데이터 & 인프라.** `events`(위), `disclosures`/`disclosure_texts`, `fundamentals`(`croesus/fundamentals/repository.py`),
`prices_daily`. **CAAR 엔진 재사용**: `experiments/events_impact/analysis/event_study.py::compute_event_study(event_dates, prices, ...)`
(카테고리 무관) + 1차의 Jordà LP(`experiments/market_signals/event_impact/irf.py`).

**방법.** 이벤트를 유형/방향/크기 버킷으로 그룹 → 그룹별 CAAR(h) + 신뢰구간, 반감기/복귀. 서프라이즈 크기 분위별 drift 단조성 검정(진짜 PEAD면 상위 서프라이즈일수록 drift↑).

**기준선/통제.** 시장/섹터 조정 수익률(단순 raw 아님), 무작위 날짜 placebo 이벤트로 귀무.

**산출물.** 이벤트유형×방향 CAAR 곡선, 서프라이즈 분위별 drift, 요약표, FINDINGS.

**성공 기준.** 최소 한 이벤트 유형에서 통제 후 유의한 drift, 서프라이즈 크기와 단조 관계. n이 커서 1차보다 신뢰구간이 훨씬 좁아야 정상.

**함정.** 사건 겹침(LP로 통제), 공시일 타임존/거래일 정렬, survivorship, 소형주 유동성.

**규모.** 중~대(3b 중, 3c 대).

---

## ② 변동성 예측 + 리스크 타게팅

**한 줄.** 수익률은 예측 못 해도 **변동성은 예측 가능**하다 — 이를 드로다운 제어에 쓸 수 있는가?

**동기.** 1차의 null(수익률/방향 예측 실패)을 뒤집는 방향. 변동성은 강한 자기상관(vol clustering)이 있어 예측이 잘 된다.
Croesus 포트폴리오 risk-gate(Phase E)·포지션 사이징에 직접 유용.

**가설.** (a) 실현변동성은 GARCH/EWMA로 naive(직전 실현변동성)보다 유의하게 잘 예측된다.
(b) 예측 변동성으로 노출을 조절(vol-targeting)하면 buy-and-hold 대비 위험조정수익(Sharpe↑, MaxDD↓)이 개선된다.

**데이터 & 인프라.** `prices_daily`만으로 충분(신규 데이터 0). 대안 리스크 신호로 `macro_scores.amplifier_score`
(`croesus/macro/indicators/amplifier.py`, 0~100 스트레스)와 비교. 백테스트 지표는 `croesus/backtest/metrics.py`
(`sharpe`, `max_drawdown`, `cagr`) 재사용 가능.

**방법.**
1. 실현변동성(rolling std, Parkinson 등) 타깃. 예측기: EWMA(RiskMetrics λ=0.94), GARCH(1,1), (보너스) TimesFM 구간예측.
2. 변동성 예측 평가: QLIKE/MSE, naive 대비 skill(1차 TimesFM 프로토콜 재사용, `timesfm_eval/metrics.py` 패턴).
3. vol-targeting 오버레이: 노출 $\propto \sigma_\text{target}/\hat\sigma_t$, 지수/포트폴리오에 적용 → Sharpe·MaxDD·턴오버, 비용 차감.
4. amplifier_score를 리스크 신호로 쓴 버전과 비교(거시 vs 통계).

**기준선.** buy-and-hold, 고정 노출, naive vol(직전값). 비용 bps 민감도.

**산출물.** vol 예측 정확도 표, vol-targeting 자산곡선 vs B&H, 드로다운 비교, FINDINGS.

**성공 기준.** vol 예측이 naive를 이기고(거의 확실), vol-targeting이 비용 후 MaxDD를 유의하게 낮춘다. 이건 positive가 나올 가능성이 높은 실험.

**함정.** vol-targeting의 turnover 비용, 레버리지 가정, target 튜닝 과적합(고정 규칙 사용).

**규모.** 중.

---

## ④ 레짐 조건부 팩터 성과

**한 줄.** 팩터 프리미엄·드로다운이 거시 레짐(성장×인플레)에 따라 달라지는가?

**동기.** 무조건 신호는 씻겨나가도 조건부 신호는 남는다. Croesus는 이미 레짐 엔진을 가지고 있으니(ADR 0004),
"어떤 레짐에서 어떤 팩터가 통하나"를 알면 screening 가중치 조정(이미 있는 one-way 의존)에 근거를 준다.

**가설.** 팩터 롱숏 수익률(예: momentum, low-vol, value)이 레짐별로 유의하게 다르다
(예: momentum은 Goldilocks에서 강하고 Deflation에서 약함).

**데이터 & 인프라.** 레짐 라벨: `macro_scores(date, regime∈{Goldilocks,Reflation,Stagflation,Deflation}, growth_direction,
inflation_direction, amplifier_score)` (`croesus/macro/engine.py::compute_macro_state`). 팩터 수익률: 실험 ①의 Q5−Q1
롱숏 시계열 재사용(①을 먼저 하면 이건 조건부 분해만 추가). 백테스트 지표 재사용.

**방법.** ①의 팩터 롱숏 수익률을 레짐별로 분할 → 레짐×팩터 평균수익·Sharpe·t-stat 표. 레짐 전이 시점 이벤트 스터디(레짐 바뀔 때 팩터 급변?). 레짐이 과거에 얼마나 지속·정확했는지(라벨 안정성) 함께 보고.

**기준선.** 무조건(전기간) 팩터 성과 대비 조건부의 개선. 레짐 라벨 셔플 placebo로 귀무.

**산출물.** 레짐×팩터 히트맵, 레짐별 자산곡선, FINDINGS(레짐 타이밍이 값어치 있나).

**성공 기준.** 최소 한 팩터가 레짐 간 유의한 성과 차이 + 그 차이가 out-of-sample/placebo를 견딤. 레짐 라벨 자체가 노이즈면 그것도 결과.

**함정.** 레짐 라벨의 look-ahead(라벨이 미래 데이터로 산출되면 안 됨 — `compute_macro_state`가 point-in-time인지 확인),
소표본(레짐당 관측 수), 레짐 분류 자체의 불확실성.

**규모.** 중(①에 의존하면 소~중).

---

## ⑤ LLM 알파 감사 (보너스)

**한 줄.** 로컬 LLM thesis 등급이 forward 수익률과 상관이 있는가? 비싸고 핵심적인 베팅을 직접 검증.

**동기.** Croesus는 Ollama 로컬 LLM으로 thesis를 등급화한다(`thesis_grader.py`). 이 등급이 실제 예측력이 있는지 아무도 검증 안 함.

**가설.** `thesis_grades`의 등급/confidence 상위 종목이 하위보다 유의하게 높은 forward 수익률을 낸다.

**데이터 & 인프라.** `thesis_grades(asset_id, as_of_date, moat_grade, tech_grade, sector_grade, disruption_grade,
confidence, ...)` (`croesus/research/thesis_repository.py`), `intrinsic_value_bands.upside_pct`, `prices_daily`.
방법은 실험 ①의 IC/분위 프레임 그대로 적용(신호 = LLM 등급).

**방법.** 등급/confidence를 신호로 ①의 IC·Q-분위 분석. 등급 생성 시점 이후 forward 수익률만 사용(look-ahead 주의). LLM 등급 vs 기계적 팩터의 IC 비교(LLM이 추가 정보를 주나?).

**기준선.** 기계적 팩터·밸류 신호. 등급 셔플 placebo.

**산출물.** LLM 등급 IC, 분위 수익, 기계적 신호 대비 증분, FINDINGS(LLM이 값어치 있나).

**성공 기준.** LLM 등급이 유의한 IC + 기계적 팩터에 없는 증분 정보. n이 작을 수 있으니(등급된 종목 수) 신뢰구간 필수.

**함정.** 표본 작음, 등급 시점 정렬(look-ahead), LLM 모델 변경 시 비일관, 등급이 이미 가격에 반영된 정보만 복창.

**규모.** 소(①을 먼저 하면 신호만 갈아끼움).

---

## 다음 액션

새 세션에서: **"로드맵 <번호> 실험 구현해줘"** → 이 문서의 해당 미니 스펙으로 brainstorming/spec 없이 바로
설계→구현(subagent-driven)→리포트 진행. 대부분 ①의 IC/분위 프레임이 ④·⑤에 재사용되므로 **① 먼저** 권장.
