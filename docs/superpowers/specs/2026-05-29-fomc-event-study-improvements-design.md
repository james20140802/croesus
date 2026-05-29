# FOMC Event Study 개선 설계

**날짜**: 2026-05-29  
**브랜치**: experiment/events-impact-fomc  
**목적**: 기존 FOMC event study prototype의 4가지 개선 사항 구현

---

## 배경 및 동기

기존 prototype(T-14~T+10, mean-adjusted AR)에서 어느 서브그룹도 통계적 유의성이 없었다.
원인은 세 가지:

1. **레짐 혼재** — 긴축/완화 사이클이 섞여 반응 방향이 상쇄됨
2. **이상치 오염** — 2020년 3월 코로나 긴급 인하 2건이 cut 그룹 분산을 폭등시킴
3. **윈도우 노이즈** — 25거래일 윈도우에 FOMC 외 뉴스가 다수 포함됨
4. **Daily close 한계** — 2pm 발표 당일 close로는 발표 직후 반응을 정확히 측정 불가

---

## 구현 범위

### Layer A: Daily 개선 (1~3번)

기존 daily 분석 파이프라인에 통합. 전체 데이터(2010~2025) 활용.

### Layer B: Intraday 분석 (4번)

별도 섹션. yfinance 730일 제한으로 **2023-06 이후 이벤트만** 커버.  
SPY 1h 데이터 기준, FOMC 당일 2pm→4pm ET 수익률 측정.

---

## 섹션 1: 데이터 레이어

### 1-1. fomc_dates.csv — 컬럼 추가

기존 컬럼: `date, category, magnitude, scope, metadata`  
추가 컬럼: `regime, is_emergency`

**`regime` 레이블 정의:**

| 기간 | regime | 설명 |
|------|--------|------|
| 2010-01-27 ~ 2015-11-18 | `hold` | ZIRP, 제로금리 동결 |
| 2015-12-16 ~ 2018-12-19 | `tightening` | 1차 긴축 사이클 |
| 2019-01-30 ~ 2019-06-19 | `hold` | 관망 (pivot 이전) |
| 2019-07-31 ~ 2020-01-29 | `easing` | 예방적 인하 3회 |
| 2020-03-03, 2020-03-15 | `crisis` | 긴급 인하 (COVID) |
| 2020-04-29 ~ 2022-01-26 | `hold` | 코로나 ZIRP |
| 2022-03-16 ~ 2023-07-26 | `tightening` | 2차 긴축 (인플레) |
| 2023-09-20 ~ 2024-07-31 | `hold` | 정점 유지 |
| 2024-09-18 ~ | `easing` | 현재 완화 사이클 |

**`is_emergency` 값:**
- `true`: 2020-03-03, 2020-03-15 (긴급 인하)
- `false`: 나머지 전체

### 1-2. data/intraday.py — 신규 파일

SPY 1h intraday 데이터 fetcher.

```
fetch_intraday_fomc(event_dates: list[date]) -> pd.DataFrame

반환 컬럼:
  event_date  DATE
  open_2pm    DOUBLE  -- 2:00pm ET 바 시가
  close_4pm   DOUBLE  -- 4:00pm ET 바 종가
  return_2pm_4pm DOUBLE  -- (close_4pm / open_2pm) - 1

내부 동작:
  1. DuckDB prices_intraday 테이블 확인 (캐싱)
  2. 미캐싱 구간 yfinance SPY interval="1h" 로 fetch
  3. 미국/뉴욕 시간대 변환 후 2pm/4pm 바 추출
  4. 캐싱 후 반환

DuckDB 테이블 스키마:
  CREATE TABLE IF NOT EXISTS prices_intraday (
      asset_id  TEXT,
      datetime  TIMESTAMP,
      open      DOUBLE,
      high      DOUBLE,
      low       DOUBLE,
      close     DOUBLE,
      volume    BIGINT,
      source    TEXT,
      PRIMARY KEY (asset_id, datetime)
  )

제약:
  - ticker: SPY (^GSPC는 intraday 지원 불안정)
  - 커버리지: 최근 730일 (yfinance 제한)
  - 타임존: America/New_York 기준으로 처리
```

---

## 섹션 2: 분석 레이어

### 2-1. main.py — CATEGORIES 변경

**① exclude_emergency 플래그 추가**

```python
"fomc": {
    "exclude_emergency": True,  # is_emergency=True 행 주 분석에서 제외
    ...
}
```

main() 루프에서 events_df 로드 직후, `base_df`(필터링됨)와 `full_df`(전체) 분리:
```python
full_df = events_df.copy()
if cfg.get("exclude_emergency"):
    base_df = events_df[events_df["is_emergency"] != True]
else:
    base_df = events_df
```
- 주 분석 및 hike/hold/cut/tightening/easing 서브그룹: `base_df` 사용
- crisis 서브그룹: `full_df` 사용 (긴급 이벤트가 곧 crisis이므로)

**② 레짐 기반 서브그룹 추가**

기존 hike/hold/cut 유지하고 아래 3개 추가:
```python
"subgroups": {
    # 기존 (base_df 기준)
    "hike":       lambda df: df[df["magnitude"].fillna(0) > 0],
    "hold":       lambda df: df[df["magnitude"].fillna(0) == 0],
    "cut":        lambda df: df[df["magnitude"].fillna(0) < 0],
    # 신규 (base_df 기준)
    "tightening": lambda df: df[df["regime"] == "tightening"],
    "easing":     lambda df: df[df["regime"] == "easing"],
    # crisis는 full_df 기준 — main()에서 특수 처리
    "crisis":     lambda df: df[df["regime"] == "crisis"],
}
```

crisis 서브그룹은 n=2이므로 t-test 출력은 생략하고 개별 이벤트 CAR만 출력.

**③ extra_windows — 짧은 윈도우 추가 패스**

```python
"extra_windows": [
    {
        "name": "short",
        "event_window": (-1, 1),        # T-1 ~ T+1, 3거래일
        "estimation_window": (-30, -2), # 짧은 estimation에 맞게 축소
    }
]
```

결과 파일: `fomc_short_per_event.csv`, `fomc_short_per_day.csv` 등  
(`fomc_*` 기존 파일 덮어쓰지 않음)

main() 루프 내 기존 분석 직후에 extra_windows 루프 추가:
```
for w in cfg.get("extra_windows", []):
    prefix = f"{category}_{w['name']}"
    → base_df 기준 (emergency 제외)
    → 전체 카테고리만 실행 (서브그룹 없음 — 짧은 윈도우의 목적은 signal 검출이지 분해가 아님)
    → compute_event_study with w['event_window'], w['estimation_window']
    → save to results/{prefix}_*.csv
    → visualize with same plot functions
    → print summary
```

### 2-2. analysis/intraday_study.py — 신규 파일

```
compute_intraday_impact(
    event_dates: list[date],
    intraday_df: pd.DataFrame,
) -> dict[str, pd.DataFrame]

반환:
  "per_event": DataFrame[event_date, return_2pm_4pm]
  "summary":   DataFrame[n, mean, std, t_stat, p_value]

동작:
  - event_dates 중 intraday_df에 있는 날만 처리
  - 누락 날짜는 경고 출력 후 스킵
  - t-stat/p-value는 기존 stats.summarize_category 로직 재사용
```

main.py에 별도 섹션으로 추가 (기존 daily 분석 루프 완료 후):
```python
# === INTRADAY ANALYSIS (SPY 2pm→4pm, 2023-06~) ===
```

---

## 파일 변경 목록

| 파일 | 변경 유형 | 내용 |
|------|-----------|------|
| `events/fomc_dates.csv` | 수정 | `regime`, `is_emergency` 컬럼 추가 |
| `events/fomc.py` | 수정 | 새 컬럼 로드 처리 |
| `data/intraday.py` | 신규 | SPY 1h fetcher + DuckDB 캐싱 |
| `analysis/intraday_study.py` | 신규 | 2pm→4pm 수익률 계산 + 통계 |
| `main.py` | 수정 | exclude_emergency, 레짐 서브그룹, extra_windows, intraday 섹션 |

---

## 방법론 한계 (업데이트)

| 한계 | 내용 |
|------|------|
| Intraday 커버리지 | 2023-06 이후 ~8개 이벤트만. 통계 검정력 낮음 |
| 레짐 경계 주관성 | 수동 레이블이므로 pivot 시점 해석에 따라 달라질 수 있음 |
| SPY vs ^GSPC | Intraday는 SPY 사용 (배당 미조정 수익률). Daily와 직접 비교 불가 |
| Crisis 그룹 n=2 | 통계 검정 불가, 서술적 분석만 |
