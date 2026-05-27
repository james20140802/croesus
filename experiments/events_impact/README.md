# Event Impact Study

거시 이벤트가 금융 시장에 미친 영향을 측정하는 event study 프레임워크.  
FOMC 금리 결정이 S&P 500에 미친 평균 abnormal return을 계산하는 것이 첫 번째 케이스.

## 빠른 시작

```bash
cd experiments/events_impact
pip install -r requirements.txt
python main.py
```

결과는 `results/` 폴더에 저장됩니다 (CSV + PNG).

---

## 방법론

| 항목 | 값 |
|------|----|
| Target market | S&P 500 (^GSPC, `adjusted_close`) |
| AR method | Mean-adjusted |
| Estimation window | T-31 ~ T-2 (30 거래일) |
| Event window | T-1 ~ T+5 (7 거래일) |
| Expected return | `mean(daily returns in estimation window)` |
| Abnormal return | `AR_t = actual_return_t - expected_return` |
| CAR | `sum(AR_t for t in event_window)` |
| t-statistic | `mean(CAR) / (std(CAR) / sqrt(n))`, cross-sectional |
| p-value | 정규근사 (`math.erfc`) |
| CI band | `mean ± 1.96 × std_err`, per-event cumulative AR 기반 |

### 가정 사항

1. **Returns**: adjusted close의 simple % return (`pct_change()`). Log return이나 excess return은 사용하지 않음.
2. **거래일**: yfinance가 반환하는 영업일. 캘린더 일수 아님.
3. **비거래일 이벤트**: FOMC 결정이 비거래일(예: 2020-03-15 일요일)인 경우 다음 거래일을 T=0으로 사용.
4. **데이터 필터**: estimation window 또는 event window에 데이터가 부족한 이벤트는 자동 제외. 경고 메시지 출력.
5. **AR method**: 현재 `mean_adjusted`만 구현. Market model, FF3 등은 `ar_method` 파라미터로 추후 추가 가능.
6. **FOMC dates**: 1차로 federalreserve.gov 자동 스크래핑 시도. 실패 시 `events/fomc_dates.csv` (curated, 2010~2025)로 fallback. `magnitude` 컬럼은 bp 단위 (hike=양수, cut=음수, hold=0).
7. **비교 통계**: 카테고리 비교는 동일한 estimation/event window 기준. 카테고리별로 다른 window를 쓰려면 `main.py`의 `CATEGORIES` dict에 `event_window`, `estimation_window` 키를 추가.

---

## 새 카테고리 추가하는 법

5단계면 끝납니다. `analysis/` 코드는 변경 불필요.

### 1. CSV 파일 생성

`events/<category>.csv`를 만들고 아래 헤더를 그대로 사용:

```csv
date,category,magnitude,scope,metadata
2025-04-02,tariff,145,US-China,"{""product"":""broad""}"
2025-04-09,tariff,-80,US-China,"{""type"":""pause""}"
```

- **필수**: `date`, `category`
- **선택**: `magnitude` (크기, 관세면 %, 금리면 bp), `scope` (적용 범위), `metadata` (JSON 문자열)
- 없는 컬럼은 빈 셀로 두면 자동으로 `NaN/None` 처리

### 2. 로더 모듈 생성

`events/<category>.py`:

```python
from pathlib import Path
from events.schema import load_events_csv

_CSV_PATH = Path(__file__).parent / "<category>.csv"

def get_events():
    return load_events_csv(_CSV_PATH, "<category>")
```

스크래핑이 필요하면 try/except로 자동 수집을 먼저 시도하고 CSV로 fallback하는 패턴 사용 (fomc.py 참고).

### 3. main.py 에 한 줄 추가

`CATEGORIES` dict에 추가:

```python
from events import tariff  # 추가

CATEGORIES = {
    "fomc":    {...},
    "tariff": {                         # 추가
        "loader": tariff.get_events,
        "target": "^GSPC",             # 또는 "^KS11" (KOSPI), "SOXX" 등
        "asset_id": "US_IDX_SP500",
        # 옵션: "event_window": (-2, 10),
        # 옵션: "estimation_window": (-61, -2),
    },
}
```

### 4. 실행

```bash
python main.py
```

### 5. 결과 확인

`results/` 폴더에 자동 생성:
- `tariff_per_event.csv` — 이벤트별 CAR
- `tariff_per_day.csv` — 이벤트×일별 AR
- `tariff_avg_ar_bar.png` — 평균 AR bar chart
- `tariff_cumulative_car.png` — 누적 CAAR + 95% CI
- `tariff_car_histogram.png` — CAR 분포
- `category_comparison.png` — 업데이트된 카테고리 비교

**다른 타겟 시장** (예: KOSPI): `CATEGORIES`의 `target`에 `"^KS11"`, `asset_id`에 `"KR_IDX_KOSPI"` 지정.

---

## 프로젝트 구조

```
experiments/events_impact/
├── main.py              # 파이프라인 entrypoint (카테고리 레지스트리)
├── config.py            # 경로 설정 (DB_PATH, RESULTS_DIR 등)
├── requirements.txt
├── events/
│   ├── schema.py        # 공통 스키마 (load_events_csv)
│   ├── fomc.py          # FOMC 로더 (스크래핑 + CSV fallback)
│   ├── fomc_dates.csv   # curated FOMC 결정일 (2010~2025)
│   ├── dummy_macro.py   # 확장성 증명용 더미
│   └── dummy_macro.csv
├── data/
│   └── prices.py        # yfinance → DuckDB read-through cache
├── analysis/
│   ├── event_study.py   # AR/CAR 계산 (카테고리 무관)
│   ├── stats.py         # 집계 통계 + 카테고리 비교
│   └── viz.py           # 시각화 (4종)
└── results/             # 출력 (.gitignored)
```

---

## 데이터 저장

ADR 0002에 따라 `storage/croesus.duckdb` (repo 루트)를 공유 사용.

| 테이블 | 내용 |
|--------|------|
| `prices_daily` | S&P 500 일별 가격 (asset_id=`US_IDX_SP500`) |
| `events` | 전체 카테고리 이벤트 일자 (`(date, category)` PK) |

---

## 결과 해석 가이드

### t-statistic 기준

| 범위 | 해석 |
|------|------|
| \|t\| > 2.0 | 통계적으로 유의 (p < 0.05, 정규근사) |
| 1.64 < \|t\| < 2.0 | 경계선상 유의 (p < 0.10) |
| \|t\| < 1.64 | 비유의 |

### CV (Coefficient of Variation = std/|mean|) 기준

| CV | 해석 |
|----|------|
| > 3 | 이벤트 간 편차 매우 큼 — 평균 해석 주의 |
| 1.5 ~ 3 | 편차 큼 |
| < 1.5 | 편차 보통 이하 |

FOMC 이벤트는 시장 상황에 따라 반응이 크게 다르므로 (금리 인상 사이클 vs 긴급 인하) CV가 높게 나올 수 있음. hike/hold/cut별로 서브그룹 분석을 원하면 `magnitude` 컬럼을 기준으로 `events_df`를 필터링 후 `compute_event_study()`를 다시 호출하면 됨.
