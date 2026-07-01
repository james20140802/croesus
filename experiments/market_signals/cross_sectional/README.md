# 로드맵 ① — Cross-sectional Information Coefficient

Croesus가 계산하는 **가격 기반 공통 팩터**가 실제로 forward 종목 수익률을 예측하는지
cross-sectional IC와 분위 롱숏으로 검증한다.

## 왜 가격 팩터만인가 (스코프)

`factor_values` 테이블은 **역사화되어 있지 않다** — 파이프라인이 매일 앞으로만 계산해
2026-05-27 이후 ~5주(17개 날짜)만 존재한다. forward 수익률(21/63/126 거래일)을 계산할
과거 시점이 없으므로 fundamental/valuation/DCF/LLM 신호는 **지금 과거검증이 불가능**하다.

반면 `prices_daily`는 2009~2026 전체가 있어, `croesus/factors/common.py`의 가격 팩터
(`momentum_1m/3m/6m`, `volatility_3m`, `liquidity_1m`, `above_200d_ma`, `beta_1y`)를
**동일 정의로 과거 전체에 대해 재계산**할 수 있다. 이 실험은 그 7개 팩터를 검증한다.
(fundamental/LLM 신호 검증은 데이터 역사화 이후 — 로드맵 ③/⑤ 사안.)

## 실행

```bash
# 저장소 루트에서
python -m experiments.market_signals.cross_sectional.run
# 소스 DB를 명시하려면:
CROESUS_SOURCE_DB=/path/to/croesus.duckdb python -m experiments.market_signals.cross_sectional.run
```

소스 DB는 항상 **read-only**로 열린다(웹 서버 등 동시 쓰기 프로세스를 막지 않음).
워크트리의 stale DB 대신 메인 체크아웃 DB를 자동 탐지한다(`source.py`).

## 방법

1. 월별 rebalance 그리드(각 달의 마지막 거래일, 2010~)마다 종목 cross-section 구성:
   각 종목의 as-of 팩터값(과거 슬라이스만) + h∈{21,63,126}일 forward 수익률(adjusted_close).
2. 시점별 Spearman IC → 평균 IC, Newey-West t-stat, IC IR(=mean/std), hit rate, IC decay.
3. 5분위 Q5−Q1 롱숏(등가중) 시계열 → 누적/Sharpe/MaxDD, 회전율 기반 비용(0/10/20bps) 민감도.
4. 순열검정: 각 시점 신호를 셔플해 IC=0 귀무 분포 → 관측 평균 IC와 비교.

## 산출물 (`results/cross_sectional/`, gitignore)

- `panel.parquet` — 원시 long 패널(date, asset_id, factor_name, value, fwd_21/63/126)
- `ic_summary.csv` — 팩터×horizon IC 통계
- `longshort_summary.csv` — Q5−Q1 성과(비용 bps별)
- `perdate_<factor>_<h>.csv` — 시점별 IC/롱숏/n/회전율
- `permutation.csv` — 관측 vs 셔플 귀무 평균 IC

## 한계 (자기기만 방지)

- **다중검정**: 7팩터×3horizon=21개 검정 — |t|>2도 일부는 우연. 순열 귀무·decay 일관성으로 교차확인.
- **Survivorship**: `assets`는 현재 상장 종목만 → 상장폐지분 누락으로 결과가 상방편향. 결론 해석 시 감안.
- **adjusted_close 편차**: Croesus는 `close`를 쓰지만 여기선 배당조정 total-return을 위해 adjusted 사용.
- **등가중 소형주**: 분위 등가중은 소형주 비중이 커 유동성/거래비용에 민감.
- **look-ahead 없음**: 팩터는 `[:as_of]`만, forward 수익률은 `[as_of:as_of+h]`만 사용.
