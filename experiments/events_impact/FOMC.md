# FOMC Event Study — 분석 방법론

## 개요

FOMC 금리 결정이 S&P 500에 미치는 영향을 측정하기 위해
**Mean-Adjusted Abnormal Return** 방법론 기반의 event study를 수행했다.
분석 기간: 2010-01-27 ~ 2025-06-18 (총 125개 FOMC 결정일).

---

## 1. Event Study 기본 방법론

### 수익률 계산

일별 수익률은 adjusted close 기준 단순 수익률을 사용한다.

$$r_t = \frac{P_t - P_{t-1}}{P_{t-1}}$$

### 윈도우 구조

| 윈도우 | 범위 | 거래일 수 | 용도 |
|--------|------|-----------|------|
| Estimation window | T-80 ~ T-15 | 66 거래일 | 정상 수익률 추정 |
| Event window | T-14 ~ T+10 | 25 거래일 | 비정상 수익률 관찰 |

T = FOMC 결정 발표일 (비거래일인 경우 다음 거래일로 이동).
두 윈도우는 겹치지 않는다 (estimation 종료 T-15, event 시작 T-14).

### 정상 수익률 (Expected Return)

Mean-Adjusted 방법: estimation window 일별 수익률의 단순 평균.

$$\hat{r} = \frac{1}{66} \sum_{t=-80}^{-15} r_t$$

시장 모델(Market Model)이나 Fama-French 3-factor 모델은 사용하지 않는다.
단순 평균이 갖는 해석의 투명성을 우선시했고,
선행 연구(Brown & Warner 1985)에서도 짧은 이벤트 윈도우에서
mean-adjusted와 market-adjusted 방법의 성능 차이가 크지 않음을 확인했다.

### 비정상 수익률 (Abnormal Return)

$$AR_t = r_t - \hat{r}$$

### 누적 비정상 수익률 (Cumulative Abnormal Return)

$$CAR_i = \sum_{t=-14}^{+10} AR_{i,t}$$

---

## 2. 통계 검정

### t-통계량 (Cross-Sectional)

이벤트 간 단면(cross-sectional) t-통계량을 사용한다.

$$t = \frac{\overline{CAR}}{s_{CAR} / \sqrt{n}}$$

- $\overline{CAR}$: 이벤트별 CAR의 표본 평균
- $s_{CAR}$: CAR의 표본 표준편차 (ddof=1)
- $n$: 이벤트 수

### p-value

정규 근사(normal approximation)를 사용한다. scipy 의존성을 피하기 위해
`math.erfc`로 계산한다.

$$p = \text{erfc}\!\left(\frac{|t|}{\sqrt{2}}\right)$$

### 유의 기준

| 기준 | 표기 |
|------|------|
| p < 0.01 | ★★★ |
| p < 0.05 | ★★  |
| p < 0.10 | ★   |

---

## 3. 서브그룹 분석

### 결정 유형별 (hike / hold / cut)

`fomc_dates.csv`의 `magnitude` 컬럼 기준으로 분류한다.

| 분류 | 조건 |
|------|------|
| hike | magnitude > 0 |
| hold | magnitude = 0 또는 NaN |
| cut  | magnitude < 0 |

magnitude 단위: basis point (bp). 예) +25 = 25bp 인상.

### Surprise 분류 (hawkish / neutral / dovish)

시장의 사전 기대와 비교한 서프라이즈 크기를 측정한다.

**Proxy**: FOMC 당일(T=0) 2년물 미국 국채 수익률 변화량

$$\Delta\text{2yr}_t = \text{DGS2}_{T=0} - \text{DGS2}_{T=-1} \quad (\text{단위: bp})$$

데이터 출처: FRED `DGS2` 시리즈 (무료, API 키 불필요).

| 분류 | 조건 |
|------|------|
| hawkish_surprise | $\Delta\text{2yr} > +5\,\text{bp}$ |
| neutral | $|\Delta\text{2yr}| \leq 5\,\text{bp}$ |
| dovish_surprise | $\Delta\text{2yr} < -5\,\text{bp}$ |

임계값 5bp는 일반적인 국채 시장의 일중 노이즈 수준을 고려한 값이다.
(민감도 분석: `run_surprise_analysis(threshold_bp=...)` 인자로 조정 가능.)

**2년물을 사용하는 이유**: 2년물 국채는 단기 통화정책 기대에 가장 민감하게 반응하는 만기다.
FF Futures 일중 가격(Kuttner 2001 방법론)이 가장 정확하지만 유료 데이터가 필요하므로
2년물 수익률 변화를 무료 대용치(proxy)로 사용한다.

---

## 4. 신뢰구간 (CI Band)

누적 평균 비정상 수익률 그래프의 95% CI는
이벤트별 cumulative AR의 단면 분포에서 계산한다.

$$CI_t = \overline{CAAR_t} \pm 1.96 \times \frac{s_{CAAR_t}}{\sqrt{n}}$$

$CAAR_t$: 각 이벤트의 T-14부터 T까지 AR 누적합.

---

## 5. 방법론 한계 및 주의사항

| 한계 | 내용 |
|------|------|
| Surprise proxy 부정확 | 2년물 daily Δ는 intraday FF Futures 변화보다 노이즈가 크다. FOMC 이외 뉴스(경제지표 발표 등)가 같은 날 섞일 수 있다. |
| 정규 근사 | p-value는 t-분포가 아닌 정규 근사. 소그룹(cut n=8 등)에서 과소추정 위험. |
| Multiple testing | 25 거래일 × 7 그룹 = 175개 검정을 동시에 수행하므로 Bonferroni 등 다중검정 보정 없이는 유의 결과 중 일부가 1종 오류일 수 있다. |
| FOMC 날짜 정확도 | 1차 소스는 Fed 공식 캘린더 스크래핑이며, 실패 시 `fomc_dates.csv` fallback을 사용한다. 비정기 긴급회의(2020-03-03, 2020-03-15 등)는 CSV에 수동 입력되어 있다. |
| Mean-Adjusted 방법 | 추정 기간(T-80~T-15) 중 시장 레짐 변화(예: 제로금리 구간)가 있으면 expected return 추정에 편의(bias)가 생길 수 있다. |
