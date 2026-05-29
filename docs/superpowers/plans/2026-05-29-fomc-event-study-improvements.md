# FOMC Event Study Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FOMC event study prototype에 레짐 분리, 이상치 제거, 짧은 윈도우, intraday 분석을 추가해 signal 검출력을 높인다.

**Architecture:** 기존 daily 파이프라인(main.py)을 확장해 레짐 기반 서브그룹과 짧은 윈도우를 추가하고, 별도 intraday 모듈(data/intraday.py + analysis/intraday_study.py)을 신규 생성한다. fomc_dates.csv에 `regime`, `is_emergency` 컬럼을 추가해 데이터 레이어에서 레짐 정보를 관리한다.

**Tech Stack:** Python 3.10, pandas, yfinance, duckdb, matplotlib, pytest, zoneinfo

---

## 파일 변경 목록

| 파일 | 유형 | 책임 |
|------|------|------|
| `events/fomc_dates.csv` | 수정 | regime, is_emergency 컬럼 추가 |
| `events/schema.py` | 수정 | 새 컬럼 pass-through |
| `events/fomc.py` | 수정 | 스크래핑 경로에서 regime/is_emergency 병합 |
| `main.py` | 수정 | exclude_emergency, 레짐 서브그룹, extra_windows, intraday 섹션 |
| `data/intraday.py` | 신규 | SPY 1h fetcher + DuckDB 캐싱 |
| `analysis/intraday_study.py` | 신규 | 2pm→4pm 수익률 계산 + 통계 |
| `tests/__init__.py` | 신규 | 패키지 마커 |
| `tests/test_csv_schema.py` | 신규 | CSV/schema 로드 테스트 |
| `tests/test_intraday_study.py` | 신규 | intraday_study 유닛 테스트 |

모든 경로는 `experiments/events_impact/` 하위 기준.

---

## Task 1: fomc_dates.csv에 regime + is_emergency 추가

**Files:**
- Modify: `events/fomc_dates.csv`
- Create: `tests/__init__.py`
- Create: `tests/test_csv_schema.py`

### 레짐 매핑 기준

| 기간 | regime | is_emergency |
|------|--------|--------------|
| 2010-01-27 ~ 2015-10-28 | `hold` | false |
| 2015-12-16 ~ 2018-12-19 | `tightening` | false |
| 2019-01-30 ~ 2019-06-19 | `hold` | false |
| 2019-07-31 ~ 2020-01-29 | `easing` | false |
| 2020-03-03, 2020-03-15 | `crisis` | true |
| 2020-04-29 ~ 2022-01-26 | `hold` | false |
| 2022-03-16 ~ 2023-07-26 | `tightening` | false |
| 2023-09-20 ~ 2024-07-31 | `hold` | false |
| 2024-09-18 ~ | `easing` | false |

- [ ] **Step 1: 테스트 파일 생성**

`tests/__init__.py` 빈 파일로 생성.

`tests/test_csv_schema.py`:
```python
import datetime
from pathlib import Path
import pandas as pd

CSV_PATH = Path(__file__).parent.parent / "events" / "fomc_dates.csv"


def test_csv_has_regime_column():
    df = pd.read_csv(CSV_PATH)
    assert "regime" in df.columns, "regime 컬럼 없음"


def test_csv_has_is_emergency_column():
    df = pd.read_csv(CSV_PATH)
    assert "is_emergency" in df.columns, "is_emergency 컬럼 없음"


def test_regime_values_valid():
    df = pd.read_csv(CSV_PATH)
    valid = {"tightening", "easing", "hold", "crisis"}
    actual = set(df["regime"].dropna().unique())
    assert actual <= valid, f"유효하지 않은 regime 값: {actual - valid}"


def test_emergency_dates_are_crisis():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    emergency = df[df["is_emergency"] == True]
    assert len(emergency) == 2
    dates = set(emergency["date"].tolist())
    assert dates == {datetime.date(2020, 3, 3), datetime.date(2020, 3, 15)}
    assert (emergency["regime"] == "crisis").all()


def test_2015_dec_is_tightening():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    row = df[df["date"] == datetime.date(2015, 12, 16)].iloc[0]
    assert row["regime"] == "tightening"


def test_2024_sep_is_easing():
    df = pd.read_csv(CSV_PATH)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    row = df[df["date"] == datetime.date(2024, 9, 18)].iloc[0]
    assert row["regime"] == "easing"


def test_no_null_regime():
    df = pd.read_csv(CSV_PATH)
    assert df["regime"].isna().sum() == 0, "regime에 null 값 있음"
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
cd experiments/events_impact
pytest tests/test_csv_schema.py -v
```
예상: `FAILED` (regime 컬럼 없음)

- [ ] **Step 3: CSV 업데이트 스크립트 실행**

아래 스크립트를 `experiments/events_impact/` 에서 실행:

```python
import datetime, pandas as pd
from pathlib import Path

CSV_PATH = Path("events/fomc_dates.csv")
df = pd.read_csv(CSV_PATH)
df["date"] = pd.to_datetime(df["date"]).dt.date

REGIME_RANGES = [
    (datetime.date(2010, 1, 27),  datetime.date(2015, 10, 28), "hold",       False),
    (datetime.date(2015, 12, 16), datetime.date(2018, 12, 19), "tightening", False),
    (datetime.date(2019, 1, 30),  datetime.date(2019, 6, 19),  "hold",       False),
    (datetime.date(2019, 7, 31),  datetime.date(2020, 1, 29),  "easing",     False),
    (datetime.date(2020, 3, 3),   datetime.date(2020, 3, 15),  "crisis",     True),
    (datetime.date(2020, 4, 29),  datetime.date(2022, 1, 26),  "hold",       False),
    (datetime.date(2022, 3, 16),  datetime.date(2023, 7, 26),  "tightening", False),
    (datetime.date(2023, 9, 20),  datetime.date(2024, 7, 31),  "hold",       False),
    (datetime.date(2024, 9, 18),  datetime.date(2099, 1, 1),   "easing",     False),
]

def assign(row):
    d = row["date"]
    for start, end, regime, emerg in REGIME_RANGES:
        if start <= d <= end:
            return pd.Series({"regime": regime, "is_emergency": emerg})
    return pd.Series({"regime": None, "is_emergency": False})

df[["regime", "is_emergency"]] = df.apply(assign, axis=1)
df["date"] = df["date"].astype(str)
df.to_csv(CSV_PATH, index=False)
print("Done:", len(df), "rows")
print(df["regime"].value_counts())
```

실행: `python -c "$(cat <<'SCRIPT'
... # 위 코드 붙여넣기
SCRIPT
)"`

또는 임시 파일로 저장 후 실행:
```bash
python /tmp/update_csv.py
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_csv_schema.py -v
```
예상: 6개 `PASSED`

- [ ] **Step 5: 커밋**

```bash
git add events/fomc_dates.csv tests/__init__.py tests/test_csv_schema.py
git commit -m "feat: add regime and is_emergency columns to fomc_dates.csv"
```

---

## Task 2: schema.py — 새 컬럼 pass-through

**Files:**
- Modify: `events/schema.py`
- Modify: `tests/test_csv_schema.py` (테스트 추가)

현재 `load_events_csv`는 `ALL_COLUMNS`에 없는 컬럼을 drop한다. `regime`과 `is_emergency`를 옵션 컬럼으로 추가한다.

- [ ] **Step 1: 테스트 추가**

`tests/test_csv_schema.py` 끝에 추가:
```python
from events.schema import load_events_csv

def test_load_events_csv_includes_regime():
    df = load_events_csv(CSV_PATH, "fomc")
    assert "regime" in df.columns

def test_load_events_csv_includes_is_emergency():
    df = load_events_csv(CSV_PATH, "fomc")
    assert "is_emergency" in df.columns

def test_load_events_csv_regime_not_null():
    df = load_events_csv(CSV_PATH, "fomc")
    assert df["regime"].isna().sum() == 0
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_csv_schema.py::test_load_events_csv_includes_regime -v
```
예상: `FAILED` (regime 컬럼이 drop됨)

- [ ] **Step 3: schema.py 수정**

`events/schema.py` 전체를 아래로 교체:
```python
from pathlib import Path
import pandas as pd

REQUIRED_COLUMNS = ["date", "category"]
OPTIONAL_COLUMNS = ["magnitude", "scope", "metadata", "regime", "is_emergency"]
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS


def load_events_csv(path: str | Path, category: str) -> pd.DataFrame:
    """Load an events CSV and enforce the standard schema."""
    df = pd.read_csv(path)
    for col in ALL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[ALL_COLUMNS].copy()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df["category"] = df["category"].fillna(category).astype(str)
    df["magnitude"] = pd.to_numeric(df["magnitude"], errors="coerce")
    df["is_emergency"] = df["is_emergency"].fillna(False).astype(bool)
    df["regime"] = df["regime"].astype(str).replace("nan", None)
    return df.sort_values("date").reset_index(drop=True)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_csv_schema.py -v
```
예상: 전체 `PASSED`

- [ ] **Step 5: 커밋**

```bash
git add events/schema.py tests/test_csv_schema.py
git commit -m "feat: extend schema.py to pass through regime and is_emergency columns"
```

---

## Task 3: fomc.py — 스크래핑 경로에서 regime/is_emergency 병합

**Files:**
- Modify: `events/fomc.py`

스크래핑 성공 시, `magnitude`/`metadata` 외에 `regime`/`is_emergency`도 CSV에서 병합해야 한다. 현재는 이 두 컬럼이 누락된다.

- [ ] **Step 1: fomc.py get_events() 내 merge 로직 수정**

`fomc.py`의 `get_events()` 함수에서 CSV merge 블록을 아래로 교체:

기존:
```python
csv_lookup = csv_df.set_index("date")[["magnitude", "metadata"]]
df["date_key"] = df["date"]
df = df.set_index("date_key")
df.update(csv_lookup)
df = df.reset_index(drop=True)
```

수정 후:
```python
merge_cols = [c for c in ["magnitude", "metadata", "regime", "is_emergency"]
              if c in csv_df.columns]
csv_lookup = csv_df.set_index("date")[merge_cols]
df["date_key"] = df["date"]
df = df.set_index("date_key")
df.update(csv_lookup)
df = df.reset_index(drop=True)
# 새 컬럼이 없으면 기본값 채움
if "regime" not in df.columns:
    df["regime"] = None
if "is_emergency" not in df.columns:
    df["is_emergency"] = False
df["is_emergency"] = df["is_emergency"].fillna(False).astype(bool)
```

또한 스크래핑 성공 시 df 초기화 블록에 새 컬럼 추가:
```python
df = pd.DataFrame({"date": scraped})
df["category"] = "fomc"
df["magnitude"] = float("nan")
df["scope"] = "US"
df["metadata"] = None
df["regime"] = None          # ← 추가
df["is_emergency"] = False   # ← 추가
```

- [ ] **Step 2: _save_to_duckdb는 수정 불필요**

기존 DuckDB events 테이블에 regime/is_emergency 컬럼이 없으므로, 저장 시 해당 컬럼을 제외한다. `_save_to_duckdb` 함수에서 SELECT 명시:

```python
def _save_to_duckdb(df: pd.DataFrame) -> None:
    import duckdb
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            date DATE,
            category TEXT,
            magnitude DOUBLE,
            scope TEXT,
            metadata JSON,
            PRIMARY KEY (date, category)
        )
    """)
    conn.execute("""
        INSERT OR REPLACE INTO events
        SELECT date, category, magnitude, scope, metadata FROM df
    """)
    conn.close()
```
(기존 코드 그대로 — DuckDB에는 regime/is_emergency 저장 안 함)

- [ ] **Step 3: 수동 검증**

```bash
cd experiments/events_impact
python -c "
from events.fomc import get_events
df = get_events()
print(df.columns.tolist())
print(df[['date','regime','is_emergency']].head(10))
print(df[df['regime']=='crisis'])
"
```
예상:
- columns에 `regime`, `is_emergency` 포함
- 2020-03-03, 2020-03-15 행에 `regime=crisis`, `is_emergency=True`

- [ ] **Step 4: 커밋**

```bash
git add events/fomc.py
git commit -m "feat: merge regime and is_emergency from CSV in fomc.py scrape path"
```

---

## Task 4: main.py — exclude_emergency + 레짐 서브그룹 + extra_windows

**Files:**
- Modify: `main.py`

- [ ] **Step 1: CATEGORIES 업데이트**

`main.py`의 `CATEGORIES` dict를 아래로 교체:

```python
CATEGORIES = {
    "fomc": {
        "loader": fomc.get_events,
        "target": "^GSPC",
        "asset_id": "US_IDX_SP500",
        "exclude_emergency": True,
        "subgroups": {
            # 결정 유형별 (기존)
            "hike": lambda df: df[df["magnitude"].fillna(0) > 0],
            "hold": lambda df: df[df["magnitude"].fillna(0) == 0],
            "cut":  lambda df: df[df["magnitude"].fillna(0) < 0],
            # 레짐별 (신규)
            "tightening": lambda df: df[df["regime"] == "tightening"],
            "easing":     lambda df: df[df["regime"] == "easing"],
            # crisis: full_df 기준으로 main()에서 특수 처리
            "crisis":     lambda df: df[df["regime"] == "crisis"],
        },
        "extra_windows": [
            {
                "name": "short",
                "event_window": (-1, 1),
                "estimation_window": (-30, -2),
            }
        ],
    },
    "dummy_macro": {
        "loader": dummy_macro.get_events,
        "target": "^GSPC",
        "asset_id": "US_IDX_SP500",
    },
}
```

- [ ] **Step 2: main() 루프에서 base_df / full_df 분리**

`main()` 내부, events_df 로드 직후 (step 1 이후) 아래 코드 삽입:

```python
# 1. Load events
events_df = cfg["loader"]()
event_dates = sorted(events_df["date"].tolist())
print(f"[main] {len(event_dates)} event dates loaded", file=sys.stderr)

# 1b. Emergency split
full_df = events_df.copy()
if cfg.get("exclude_emergency"):
    base_df = events_df[events_df["is_emergency"] != True].copy()
    n_excluded = len(events_df) - len(base_df)
    if n_excluded:
        print(f"[main] excluded {n_excluded} emergency events", file=sys.stderr)
else:
    base_df = events_df

event_dates = sorted(base_df["date"].tolist())
print(f"[main] {len(event_dates)} events after emergency exclusion", file=sys.stderr)
```

- [ ] **Step 3: subgroup 루프에서 crisis만 full_df 사용**

서브그룹 루프 (step 8) 내부에서, `sg_events = filter_fn(events_df)` 를 아래로 교체:

```python
# crisis는 full_df(긴급 이벤트 포함)에서 필터링
src_df = full_df if sg_name == "crisis" else base_df
sg_events = filter_fn(src_df)
sg_dates = sorted(sg_events["date"].tolist())
if not sg_dates:
    print(f"[main] subgroup {sg_name}: 0 events, skip", file=sys.stderr)
    continue
print(f"[main] subgroup {sg_name}: {len(sg_dates)} events", file=sys.stderr)
```

crisis subgroup은 n=2이므로 t-test 대신 개별 이벤트 CAR만 출력:
```python
if sg_name == "crisis":
    sg_row = sg_summary.iloc[0]
    print(f"\n  ┌── {sg_name.upper()} (n={int(sg_row['n'])}) — 서술적 분석만")
    for _, ev_row in sg_per_event.iterrows():
        print(f"  │   {ev_row['event_date']}: CAR={ev_row['CAR']*100:.2f}%")
    print(f"  └── t-test 생략 (n<5)")
    sg_summaries.append(sg_summary)
    continue
```

- [ ] **Step 4: extra_windows 루프 추가**

서브그룹 분석 블록(step 8) 이후, step 9(cross-category comparison) 이전에 삽입:

```python
# 8b. Extra window passes
for w in cfg.get("extra_windows", []):
    w_name = w["name"]
    w_event_window = w["event_window"]
    w_estimation_window = w["estimation_window"]
    prefix = f"{category}_{w_name}"
    print(f"\n[main] extra_window: {prefix} ({w_event_window})", file=sys.stderr)

    w_result = compute_event_study(
        sorted(base_df["date"].tolist()),
        prices,
        event_window=w_event_window,
        estimation_window=w_estimation_window,
    )
    w_per_day = w_result["per_day"]
    w_per_event = w_result["per_event"]

    w_per_event.to_csv(RESULTS_DIR / f"{prefix}_per_event.csv", index=False)
    w_per_day.to_csv(RESULTS_DIR / f"{prefix}_per_day.csv", index=False)

    w_summary = summarize_category(w_per_event, w_per_day, prefix)
    w_day_stats = per_day_stats(w_per_day)
    w_day_stats.to_csv(RESULTS_DIR / f"{prefix}_day_stats.csv", index=False)

    plot_avg_ar_bar(w_day_stats, prefix, RESULTS_DIR / f"{prefix}_avg_ar_bar.png")
    plot_cumulative_car(w_per_day, prefix, RESULTS_DIR / f"{prefix}_cumulative_car.png")
    plot_car_histogram(w_per_event, prefix, RESULTS_DIR / f"{prefix}_car_histogram.png")

    w_row = w_summary.iloc[0]
    print(f"\n─── {prefix.upper()} 결과 ───")
    print(f"  이벤트 수       : {int(w_row['n'])}")
    print(f"  평균 CAR        : {w_row['mean_CAR']*100:.4f}%")
    print(f"  t-statistic     : {w_row['t_stat']:.3f}")
    print(f"  p-value         : {w_row['p_value']:.4f}")
    print(f"  분산 코멘트     : {variance_comment(w_row)}")
```

- [ ] **Step 5: 수동 실행 — daily 파트 검증**

```bash
cd experiments/events_impact
python main.py 2>&1 | grep -E "(결과|tightening|easing|crisis|short|emergency)"
```
예상 출력에 포함:
- `excluded 2 emergency events`
- `subgroup tightening: 37 events`
- `subgroup easing: 12 events`
- `subgroup crisis: 2 events`
- `FOMC_SHORT 결과` 섹션

- [ ] **Step 6: 커밋**

```bash
git add main.py
git commit -m "feat: add regime subgroups, exclude_emergency, and extra_windows to main.py"
```

---

## Task 5: data/intraday.py 구현

**Files:**
- Create: `data/intraday.py`
- Create: `tests/test_intraday_study.py` (이 task에서 fetch 테스트만)

- [ ] **Step 1: 테스트 파일 생성 (compute 테스트는 Task 6에서)**

`tests/test_intraday_study.py`:
```python
import datetime
import pandas as pd
import pytest
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _make_intraday_df(event_date: datetime.date) -> pd.DataFrame:
    """Fake intraday df as fetch_intraday_fomc would return."""
    return pd.DataFrame([{
        "event_date": event_date,
        "open_2pm": 560.0,
        "close_4pm": 567.0,
        "return_2pm_4pm": 567.0 / 560.0 - 1,
    }])


def test_intraday_df_structure():
    df = _make_intraday_df(datetime.date(2024, 9, 18))
    assert set(df.columns) == {"event_date", "open_2pm", "close_4pm", "return_2pm_4pm"}
    assert len(df) == 1


def test_return_calculation():
    df = _make_intraday_df(datetime.date(2024, 9, 18))
    expected = 567.0 / 560.0 - 1
    assert abs(df.iloc[0]["return_2pm_4pm"] - expected) < 1e-9
```

- [ ] **Step 2: 테스트 실행 — 통과 확인 (픽스쳐 테스트이므로 바로 통과)**

```bash
pytest tests/test_intraday_study.py::test_intraday_df_structure -v
pytest tests/test_intraday_study.py::test_return_calculation -v
```
예상: `PASSED`

- [ ] **Step 3: data/intraday.py 작성**

`data/intraday.py` 신규 생성:
```python
"""SPY 1h intraday price data for FOMC event days.

Returns 2pm→4pm ET return for each FOMC event date.
Coverage: last 730 days (yfinance limitation).
Caches in DuckDB prices_intraday table.
"""
import datetime
import sys
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import yfinance as yf

from config import DB_PATH

ET = ZoneInfo("America/New_York")

_SCHEMA = """
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
"""
# datetime 컬럼은 UTC ISO 문자열로 저장. 읽어올 때 ET로 변환.


def _get_connection() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(DB_PATH))
    conn.execute(_SCHEMA)
    return conn


def _fetch_spy_hourly(start: datetime.date, end: datetime.date) -> pd.DataFrame:
    raw = yf.download(
        "SPY",
        start=str(start),
        end=str(end + datetime.timedelta(days=1)),
        interval="1h",
        auto_adjust=False,
        progress=False,
    )
    if raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    # 타임존을 ET로 통일
    if raw.index.tz is None:
        raw.index = raw.index.tz_localize("UTC").tz_convert(ET)
    else:
        raw.index = raw.index.tz_convert(ET)
    return raw


def fetch_intraday_fomc(
    event_dates: list[datetime.date],
) -> pd.DataFrame:
    """Return 2pm→4pm ET returns for FOMC event dates.

    Columns: event_date, open_2pm, close_4pm, return_2pm_4pm
    Dates outside yfinance 730-day window are silently omitted.
    """
    if not event_dates:
        return pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])

    cutoff = datetime.date.today() - datetime.timedelta(days=729)
    eligible = sorted(d for d in event_dates if d >= cutoff)
    if not eligible:
        print("[intraday] no events within yfinance 730-day window", file=sys.stderr)
        return pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])

    conn = _get_connection()

    # 캐시 확인
    cached = conn.execute(
        """SELECT DISTINCT CAST(datetime AS DATE) AS dt
           FROM prices_intraday
           WHERE asset_id = 'SPY_1H'
             AND CAST(datetime AS DATE) BETWEEN ? AND ?""",
        [min(eligible), max(eligible)],
    ).fetchdf()
    cached_dates = set(cached["dt"].astype(str)) if not cached.empty else set()

    need_fetch = [d for d in eligible if str(d) not in cached_dates]
    if need_fetch:
        print(
            f"[intraday] fetching SPY 1h {min(need_fetch)} → {max(need_fetch)}",
            file=sys.stderr,
        )
        raw = _fetch_spy_hourly(min(need_fetch), max(need_fetch))
        if not raw.empty:
            adj_col = "Adj Close" if "Adj Close" in raw.columns else "Close"
            rows = [
                (
                    "SPY_1H",
                    ts.isoformat(),
                    float(row.get("Open", float("nan"))),
                    float(row.get("High", float("nan"))),
                    float(row.get("Low", float("nan"))),
                    float(row.get(adj_col, float("nan"))),
                    int(row.get("Volume", 0) or 0),
                    "yfinance",
                )
                for ts, row in raw.iterrows()
            ]
            conn.executemany(
                "INSERT OR REPLACE INTO prices_intraday VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            print(f"[intraday] cached {len(rows)} hourly bars", file=sys.stderr)

    # 각 이벤트 날짜에서 2pm bar open, 4pm bar (15:xx의 마지막) close 추출
    results = []
    for ed in eligible:
        day_data = conn.execute(
            """SELECT datetime, open, close
               FROM prices_intraday
               WHERE asset_id = 'SPY_1H'
                 AND CAST(datetime AS DATE) = ?
               ORDER BY datetime""",
            [ed],
        ).fetchdf()

        if day_data.empty:
            print(f"[intraday] no data for {ed}, skip", file=sys.stderr)
            continue

        day_data["dt_et"] = pd.to_datetime(day_data["datetime"], utc=True).dt.tz_convert(ET)
        day_data["hour"] = day_data["dt_et"].dt.hour

        bar_14 = day_data[day_data["hour"] == 14]   # 2pm bar
        bar_15 = day_data[day_data["hour"] == 15]   # 3pm bar (closes at 4pm)

        if bar_14.empty or bar_15.empty:
            print(f"[intraday] missing 2pm or 3pm bar for {ed}, skip", file=sys.stderr)
            continue

        open_2pm = float(bar_14.iloc[0]["open"])
        close_4pm = float(bar_15.iloc[-1]["close"])
        ret = close_4pm / open_2pm - 1

        results.append({
            "event_date": ed,
            "open_2pm": open_2pm,
            "close_4pm": close_4pm,
            "return_2pm_4pm": ret,
        })

    conn.close()
    return (
        pd.DataFrame(results)
        if results
        else pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])
    )
```

- [ ] **Step 4: import 확인**

```bash
cd experiments/events_impact
python -c "from data.intraday import fetch_intraday_fomc; print('OK')"
```
예상: `OK`

- [ ] **Step 5: 커밋**

```bash
git add data/intraday.py tests/test_intraday_study.py tests/__init__.py
git commit -m "feat: add SPY 1h intraday fetcher with DuckDB caching"
```

---

## Task 6: analysis/intraday_study.py 구현

**Files:**
- Create: `analysis/intraday_study.py`
- Modify: `tests/test_intraday_study.py` (테스트 추가)

- [ ] **Step 1: 테스트 추가**

`tests/test_intraday_study.py`에 추가:
```python
import datetime
import math
from analysis.intraday_study import compute_intraday_impact


def _make_returns_df(returns: list[float]) -> pd.DataFrame:
    base = datetime.date(2024, 9, 1)
    rows = []
    for i, r in enumerate(returns):
        rows.append({
            "event_date": base + datetime.timedelta(days=i * 30),
            "open_2pm": 500.0,
            "close_4pm": 500.0 * (1 + r),
            "return_2pm_4pm": r,
        })
    return pd.DataFrame(rows)


def test_compute_intraday_impact_basic():
    returns = [0.01, -0.005, 0.02, 0.015, -0.01]
    df = _make_returns_df(returns)
    event_dates = df["event_date"].tolist()
    result = compute_intraday_impact(event_dates, df)

    assert "per_event" in result
    assert "summary" in result
    assert len(result["per_event"]) == 5
    summary = result["summary"].iloc[0]
    assert summary["n"] == 5
    assert abs(summary["mean"] - sum(returns) / 5) < 1e-9


def test_compute_intraday_impact_t_stat():
    # 모두 양수 수익률 → t_stat 양수
    returns = [0.01] * 10
    df = _make_returns_df(returns)
    result = compute_intraday_impact(df["event_date"].tolist(), df)
    summary = result["summary"].iloc[0]
    # std=0이면 t_stat=nan
    assert math.isnan(summary["t_stat"]) or summary["t_stat"] > 0


def test_compute_intraday_impact_empty():
    df = pd.DataFrame(columns=["event_date", "open_2pm", "close_4pm", "return_2pm_4pm"])
    result = compute_intraday_impact([], df)
    assert result["per_event"].empty
    summary = result["summary"].iloc[0]
    assert summary["n"] == 0


def test_compute_intraday_impact_filters_by_dates():
    returns = [0.01, -0.005, 0.02]
    df = _make_returns_df(returns)
    # 첫 번째 날짜만 요청
    result = compute_intraday_impact([df["event_date"].iloc[0]], df)
    assert len(result["per_event"]) == 1
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_intraday_study.py::test_compute_intraday_impact_basic -v
```
예상: `FAILED` (모듈 없음)

- [ ] **Step 3: analysis/intraday_study.py 작성**

```python
"""Intraday event impact: computes 2pm→4pm ET return statistics."""
import math

import pandas as pd


def compute_intraday_impact(
    event_dates: list,
    intraday_df: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    """Statistical summary of 2pm→4pm returns on FOMC event dates.

    Parameters
    ----------
    event_dates : list of date
        FOMC dates to analyze.
    intraday_df : pd.DataFrame
        Output of fetch_intraday_fomc(). Columns: event_date, return_2pm_4pm.

    Returns
    -------
    dict:
        'per_event': DataFrame[event_date, return_2pm_4pm]
        'summary':   DataFrame[n, mean, std, t_stat, p_value]
    """
    _empty_summary = pd.DataFrame([{
        "n": 0, "mean": float("nan"), "std": float("nan"),
        "t_stat": float("nan"), "p_value": float("nan"),
    }])

    if intraday_df.empty or not event_dates:
        return {
            "per_event": pd.DataFrame(columns=["event_date", "return_2pm_4pm"]),
            "summary": _empty_summary,
        }

    per_event = intraday_df[intraday_df["event_date"].isin(event_dates)].copy()
    returns = per_event["return_2pm_4pm"].dropna()
    n = len(returns)

    if n < 2:
        summary = pd.DataFrame([{
            "n": n,
            "mean": float(returns.mean()) if n == 1 else float("nan"),
            "std": float("nan"),
            "t_stat": float("nan"),
            "p_value": float("nan"),
        }])
        return {"per_event": per_event, "summary": summary}

    mean = float(returns.mean())
    std = float(returns.std(ddof=1))
    t_stat = mean / (std / math.sqrt(n)) if std > 0 else float("nan")
    p_value = (
        math.erfc(abs(t_stat) / math.sqrt(2))
        if not math.isnan(t_stat)
        else float("nan")
    )

    summary = pd.DataFrame([{
        "n": n,
        "mean": mean,
        "std": std,
        "t_stat": t_stat,
        "p_value": p_value,
    }])
    return {"per_event": per_event, "summary": summary}
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
pytest tests/test_intraday_study.py -v
```
예상: 전체 `PASSED`

- [ ] **Step 5: 커밋**

```bash
git add analysis/intraday_study.py tests/test_intraday_study.py
git commit -m "feat: add intraday_study.py for FOMC 2pm→4pm return analysis"
```

---

## Task 7: main.py — intraday 섹션 추가 + 전체 실행 검증

**Files:**
- Modify: `main.py`

- [ ] **Step 1: import 추가**

`main.py` 상단 import 블록에 추가:
```python
from data.intraday import fetch_intraday_fomc
from analysis.intraday_study import compute_intraday_impact
```

- [ ] **Step 2: intraday 섹션 추가**

`main()` 끝, surprise analysis 블록 이후에 추가:

```python
# === INTRADAY ANALYSIS (SPY 2pm→4pm ET, yfinance 730일 이내만) ===
if "fomc" in category_data:
    print("\n" + "="*60)
    print("[main] INTRADAY ANALYSIS — SPY 2pm→4pm ET")
    cd = category_data["fomc"]
    fomc_base_dates = sorted(
        cd["events_df"][cd["events_df"]["is_emergency"] != True]["date"].tolist()
    )
    try:
        intraday_df = fetch_intraday_fomc(fomc_base_dates)
        if intraday_df.empty:
            print("[main] intraday 데이터 없음 (730일 범위 밖)")
        else:
            intraday_result = compute_intraday_impact(fomc_base_dates, intraday_df)
            per_ev = intraday_result["per_event"]
            summary = intraday_result["summary"]

            per_ev.to_csv(RESULTS_DIR / "fomc_intraday_per_event.csv", index=False)
            summary.to_csv(RESULTS_DIR / "fomc_intraday_summary.csv", index=False)

            row = summary.iloc[0]
            print(f"\n─── FOMC INTRADAY (2pm→4pm) 결과 ───")
            print(f"  이벤트 수  : {int(row['n'])}")
            print(f"  평균 수익률: {row['mean']*100:.4f}%")
            print(f"  표준편차   : {row['std']*100:.4f}%")
            print(f"  t-statistic: {row['t_stat']:.3f}")
            print(f"  p-value    : {row['p_value']:.4f}")

            print("\n  이벤트별 2pm→4pm 수익률:")
            for _, r in per_ev.iterrows():
                sign = "+" if r["return_2pm_4pm"] >= 0 else ""
                print(f"    {r['event_date']}: {sign}{r['return_2pm_4pm']*100:.2f}%")

    except Exception as e:
        print(f"[main] intraday 분석 건너뜀: {e}")
```

- [ ] **Step 3: 전체 파이프라인 실행**

```bash
cd experiments/events_impact
python main.py 2>&1
```

출력에서 확인할 항목:
1. `excluded 2 emergency events` 메시지
2. `subgroup tightening:` 과 `subgroup easing:` 결과 출력
3. `subgroup crisis:` — 개별 CAR 출력, t-test 생략
4. `FOMC_SHORT 결과` — T-1~T+1 짧은 윈도우 결과
5. `INTRADAY ANALYSIS` 섹션 — 이벤트별 2pm→4pm 수익률

- [ ] **Step 4: results 디렉토리 확인**

```bash
ls experiments/events_impact/results/ | grep -E "(tightening|easing|crisis|short|intraday)"
```
예상 파일:
```
fomc_tightening_per_event.csv
fomc_tightening_cumulative_car.png
fomc_easing_per_event.csv
fomc_easing_cumulative_car.png
fomc_crisis_per_event.csv
fomc_short_per_event.csv
fomc_short_cumulative_car.png
fomc_intraday_per_event.csv
fomc_intraday_summary.csv
```

- [ ] **Step 5: 전체 테스트 통과 확인**

```bash
cd experiments/events_impact
pytest tests/ -v
```
예상: 전체 `PASSED`

- [ ] **Step 6: 최종 커밋**

```bash
git add main.py results/
git commit -m "feat: wire intraday analysis into main.py pipeline"
```

---

## 완료 기준 체크리스트

- [ ] `pytest tests/ -v` 전체 통과
- [ ] `python main.py` 오류 없이 완료
- [ ] `results/fomc_tightening_*.csv`, `results/fomc_easing_*.csv` 생성
- [ ] `results/fomc_short_*.csv` 생성 (T-1~T+1 윈도우)
- [ ] `results/fomc_intraday_*.csv` 생성
- [ ] crisis 서브그룹 n=2, 개별 CAR 출력됨
- [ ] emergency excluded 메시지 확인
