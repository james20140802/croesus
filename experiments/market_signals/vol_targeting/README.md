# 로드맵 ② — 변동성 예측 + 리스크 타게팅

수익률 방향은 예측 못 해도(1차 실험 null) **변동성은 예측 가능**한지, 그리고 그 예측으로
노출을 조절(vol-targeting)하면 buy-and-hold 대비 **MaxDD·Sharpe가 개선**되는지 검증한다.

## 실행

```bash
# 저장소 루트에서 — SPY(1993~) + 장기 유니버스 등가중(1990~), 월별 walk-forward
python -m experiments.market_signals.vol_targeting.run
# 부분 실행 / 빠른 스모크:
VT_ASSETS=spy VT_START_YEAR=2018 python -m experiments.market_signals.vol_targeting.run
```

SPY 전체 이력은 최초 1회 yfinance에서 받아 스크래치 DB
(`results/vol_targeting/index_history.duckdb`)에 캐시한다 — **프로덕션 DB는 건드리지 않는다**.
등가중(EW) 포트폴리오는 ①의 30년 스크래치 DB(`results/cross_sectional/long_history.duckdb`)를
재사용하므로 ①의 history 수집이 선행돼야 한다.

## 방법

1. **Walk-forward 예측**: 매 월말 t에 `returns[:t]`만으로 다음 21거래일의 연율화 변동성을 예측
   (look-ahead 없음). 예측기 3종:
   - `naive` — 직전 21일 실현변동성 (이겨야 할 기준선)
   - `ewma` — RiskMetrics EWMA(λ=0.94)
   - `garch` — GARCH(1,1) Gaussian MLE(scipy로 직접 구현, 신규 의존성 0), 월별 재적합(직전 2000일)
2. **정확도 평가**: forward 실현변동성 대비 MSE·QLIKE(분산 기준), naive 대비
   Diebold-Mariano식 검정(손실차 평균의 Newey-West t; 음수 = naive보다 우수).
3. **Vol-targeting 오버레이**: 노출 = min(cap, 0.15/σ̂), cap∈{1.0, 1.5} 고정 규칙(튜닝 금지).
   노출은 예측일 **다음 거래일**부터 적용, 월별 리밸런스, 비용 = |Δ노출|×{0,10}bps.
   `oracle`(forward 실현변동성을 그대로 사용 — look-ahead 상한선)과 `bnh`(노출 1 고정)를 병기.

## 산출물 (`results/vol_targeting/`, gitignore)

- `accuracy_<asset>.csv` — 예측기별 MSE/QLIKE/DM t-stat
- `overlay_<asset>.csv` — 전략×cap×cost 성과(Sharpe/MaxDD/누적/회전율/평균노출)
- `perdate_<asset>.csv` — 월별 예측 3종 + forward 실현변동성
- `curve_<asset>_cap{c}_c{bps}.csv` — 전략별 일별 수익률(자산곡선 재구성용)

## 한계 (자기기만 방지)

- **cash 수익률 0% 가정**: 노출<1일 때 잔여분이 무수익 — vol-targeting에 **보수적**(불리한) 가정.
- **σ_target=0.15 / cap 고정**: 파라미터 튜닝을 하지 않았다(과적합 방지). oracle과의 격차가 개선 여지.
- **EW 자산의 survivorship**: 수익 *수준*은 상방편향이지만, 오버레이 vs B&H는 같은 포트폴리오의
  노출 조절이므로 **내부 비교는 공정**하다.
- **`macro_scores.amplifier_score` 비교 불가**: 테이블에 14일치(2026-06-06~)만 존재 —
  ①의 `factor_values`와 같은 역사화 갭. 거시 vs 통계 리스크 신호 비교는 스냅샷이 쌓인 뒤에.
- **자산 2개(SPY, EW)뿐**: 다중검정 문제는 없지만 일반화도 제한적.
- ①의 low-vol 아노말리(종목 간 cross-section)와 여기의 vol-targeting(시계열)은 **다른 주장** —
  이 실험은 시계열 변동성 예측성만 다룬다.
