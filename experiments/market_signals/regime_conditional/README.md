# 로드맵 ④ — 레짐 조건부 팩터 성과

팩터 롱숏 프리미엄(①의 30년 월별 시계열)이 거시 레짐(성장×인플레 4분면)에 따라 유의하게
달라지는지 검증한다. "무조건 신호는 씻겨나가도 조건부 신호는 남는다" 가설의 직접 검정.

## 실행

```bash
# 저장소 루트에서 — 라벨 437개월 × 라벨 2변형 × 팩터 7종
python3 -m experiments.market_signals.regime_conditional.run
# 스모크:
RC_FACTORS=momentum_6m python3 -m experiments.market_signals.regime_conditional.run
```

①의 `results/cross_sectional_long/perdate_<factor>_21.csv`(30년 월별 Q5−Q1 롱숏)와
스크래치 DB를 재사용하므로 ①의 long-history 실행이 선행돼야 한다. FRED 데이터는
`results/regime_conditional/fred_cache/`에 자동 캐시된다(API 키 불요).

## 설계 결정 — 레짐 라벨 소급 재계산 (③과 같은 패턴)

`macro_scores`는 14일치뿐(역사화 갭)이라 로드맵 원문대로는 불가. 대신 프로덕션 투표 함수
(`croesus/macro/indicators/{growth,inflation}.py`)를 **그대로 import**해 FRED 원계열의
point-in-time 뷰로 월말마다 소급 실행한다:

- **발표 시차**: 관측일→이용가능일 보수적 오프셋(월간 지표 40~60일, 주간 7일, GDP 121일,
  일간 시장 시계열 1~3일) — look-ahead 방지. 단 데이터 개정(revision)은 미통제(FINDINGS §5).
- **라벨 2변형**: `prod`(프로덕션 충실 — 레벨 그대로)와 `yoy`(CPI/PCE/임금을 12개월 YoY로
  변환 후 동일 투표). 사전 조사에서 레벨 기울기는 98~99% 양수로 인플레 투표가 퇴화함을
  확인했기 때문(FINDINGS §1).
- **sanity**: 소급 prod 라벨을 프로덕션 `macro_scores` 14일치와 대조(일치 확인).

## 방법

1. 팩터 롱숏(h=21, 월별 비중첩)을 리밸런스 시점에 알려진 최신 라벨로 분해 →
   레짐별 평균/t/Sharpe/n.
2. 귀무: 라벨 시계열의 **모든 원형 시프트**(run 구조·지속성 완전 보존)로 between-group
   분산 통계량의 placebo 분포 → p값.
3. 시장(EW) 다음 달 수익의 레짐 조건부 표(레짐이 시장 자체를 예측하는가).
4. 레짐 전환 직후 1개월 vs 지속 구간 비교.

## 산출물 (`results/regime_conditional/`, gitignore)

- `regimes_{prod,yoy}.csv` — 월별 라벨(성장/인플레 방향, 신뢰도 포함)
- `regime_summary.csv`, `transitions_{prod,yoy}.csv` — 분포·run 길이·전이 행렬
- `factor_regime_table.csv` — 변형×팩터×레짐 {n, mean, t, sharpe}
- `placebo.csv` — 변형×팩터 between-stat과 shift placebo p
- `market_by_regime.csv`, `post_change.csv`

## 한계 (요약 — 상세는 FINDINGS §5)

- FRED 최신 vintage 사용(개정 미통제), ISM PMI 제외(30년 소급 불가), T5YIE 2003~/CES 2006~.
- yoy 라벨도 지속성이 1.4~2.1개월로 짧다 — 레짐 타이밍의 실효성 한계.
- ①의 survivorship 유니버스를 물려받는다.

결과 해석은 `FINDINGS.md` 참조.
