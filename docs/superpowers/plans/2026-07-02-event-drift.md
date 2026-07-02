# 로드맵 ③ — 종목 이벤트 스터디 (PEAD류) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 종목 단위 사건(3σ 가격 급변, 2σ 거래량 급증) 이후 [T+1, T+60] 시장조정 누적초과수익(CAAR)에 유의한 표류(drift)가 존재하는지 30년·523종목·수만 건 표본으로 검증한다.

**Architecture:** `croesus/events/detectors.py`의 가격 기반 탐지 규칙 2종을 30년 스크래치 가격 이력(①의 `long_history.duckdb`)에 벡터화 소급 적용해 이벤트 패널을 만들고, 시장(EW)조정 CAR 경로 → 날짜 군집(Fama-MacBeth식) CAAR + Newey-West t → 무작위 날짜 placebo → 크기 분위 단조성 → calendar-time 롱숏 포트폴리오(비용 포함) 순으로 평가한다.

**Tech Stack:** pandas, numpy, duckdb (기존 requirements.txt로 충분 — 신규 의존성 0). 재사용: `cross_sectional.stats.newey_west_se`, `cross_sectional.portfolio.perf_summary`, `cross_sectional.history.load_long_history`, `vol_targeting.data.equal_weight_returns`, `common.config.RESULTS_DIR`.

## Global Constraints

- 프로덕션 DB `storage/croesus.duckdb`는 **읽기 전용으로도 열지 않는다** (스크래치 DB만 사용).
- 루트 `pyproject.toml` 수정 금지 — 실험 의존성은 `experiments/market_signals/requirements.txt`에만 (이번엔 추가 없음).
- `results/`는 gitignore — 산출물 CSV는 커밋하지 않는다.
- 파라미터는 프로덕션 탐지 규칙과 동일하게 고정(RETURN_WINDOW=63, RETURN_SIGMA_MULT=3.0, VOLUME_WINDOW=21, VOLUME_Z_THRESHOLD=2.0) — 튜닝 금지.
- 커밋: gitmoji + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01CcgfrARnre7uKSmo2AQnQG`.
- 테스트 실행: 저장소 루트에서 `python3 -m pytest experiments/market_signals/tests/ -q`.

## 설계 결정 (데이터 갭 대응)

로드맵 원문의 3b(`events` 테이블 사용)는 **그대로는 불가능**: `events`는 2026-06-26~06-30 5일치뿐(역사화 갭), `disclosures`는 0행(3a도 불가). 대신 **탐지 규칙 자체를 소급 재계산**한다 — `detect_abnormal_return`(|r_t| ≥ 3×trailing 63d σ)과 `detect_abnormal_volume`(volume z ≥ 2 vs trailing 21d, 상방만)은 **trailing 윈도만 사용하므로 과거 적용에 look-ahead가 없다**. 사전 표본 추정: abnormal_return 약 57k건(up 29k/down 28k), abnormal_volume 약 259k건.

- 시장 조정: 같은 유니버스의 EW 일수익률 차감(내부 비교 — survivorship이 수준은 부풀려도 이벤트 vs 비이벤트 비교는 공정).
- 추론: 같은 날 이벤트들의 CAR을 날짜 평균으로 축약(교차상관 처리) 후 날짜 시계열에 Newey-West(lags=h) t.
- 겹침 축소: 같은 (자산, 이벤트유형) 내 21거래일 내 후속 이벤트 제거(dedupe).
- 귀무: 자산별 이벤트 개수를 보존한 무작위 날짜 placebo(seed 고정).
- 섹터 중립화는 생략(스크래치 DB에 섹터 없음) — 한계로 문서화.

---

### Task 1: 이벤트 소급 탐지 (`detect.py`)

**Files:**
- Create: `experiments/market_signals/event_drift/__init__.py` (빈 파일)
- Create: `experiments/market_signals/event_drift/detect.py`
- Test: `experiments/market_signals/tests/test_ed_detect.py`

**Interfaces:**
- Produces: `scan_asset_events(prices: pd.DataFrame) -> pd.DataFrame` — prices는 date 인덱스 + close/volume 컬럼(`load_long_history` 출력과 동일). 반환 컬럼: `date, pos, event_type, direction, magnitude` (`pos`는 prices 프레임의 정수 행 위치).
- Produces: `dedupe_events(events: pd.DataFrame, min_gap: int = 21) -> pd.DataFrame` — events는 `asset_id` 컬럼 포함; (asset_id, event_type)별로 직전 채택 이벤트와 pos 차이 ≥ min_gap인 것만 유지.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""로드맵 ③ — 이벤트 소급 탐지 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.detect import dedupe_events, scan_asset_events


def _prices(returns, volumes):
    idx = pd.bdate_range("2020-01-01", periods=len(returns) + 1)
    close = 100 * np.cumprod([1.0] + [1 + r for r in returns])
    return pd.DataFrame({"close": close, "volume": [1e6] + list(volumes)}, index=idx)


def test_scan_detects_3sigma_return_event():
    rng = np.random.RandomState(0)
    rets = list(rng.normal(0, 0.01, 100))
    rets[80] = 0.10  # ≈10σ up-move
    px = _prices(rets, [1e6] * 100)
    ev = scan_asset_events(px)
    hits = ev[(ev["event_type"] == "abnormal_return") & (ev["pos"] == 81)]
    assert len(hits) == 1
    assert hits.iloc[0]["direction"] == "up"
    assert hits.iloc[0]["magnitude"] > 3.0
    assert hits.iloc[0]["date"] == px.index[81]


def test_scan_detects_volume_spike_up_only():
    rng = np.random.RandomState(1)
    rets = list(rng.normal(0, 0.01, 60))
    vols = list(rng.normal(1e6, 5e4, 60))
    vols[50] = 3e6   # 큰 양의 z
    vols[55] = 1e3   # 낮은 거래량은 이벤트 아님
    ev = scan_asset_events(_prices(rets, vols))
    vol_ev = ev[ev["event_type"] == "abnormal_volume"]
    assert 51 in set(vol_ev["pos"])
    assert 56 not in set(vol_ev["pos"])
    assert (vol_ev["direction"] == "up").all()


def test_scan_insufficient_history_no_events():
    rets = [0.2] * 10  # 워밍업(63일) 미만
    ev = scan_asset_events(_prices(rets, [1e6] * 10))
    assert len(ev[ev["event_type"] == "abnormal_return"]) == 0


def test_dedupe_keeps_first_within_gap():
    ev = pd.DataFrame({
        "asset_id": ["A", "A", "A", "B"],
        "date": pd.to_datetime(["2020-01-01", "2020-01-10", "2020-03-01", "2020-01-10"]),
        "pos": [100, 110, 150, 110],
        "event_type": ["abnormal_return"] * 4,
        "direction": ["up"] * 4,
        "magnitude": [3.5, 4.0, 3.2, 5.0],
    })
    out = dedupe_events(ev, min_gap=21)
    # A: 100 채택, 110 제거(gap 10), 150 채택(gap 50); B: 채택
    assert list(out["pos"]) == [100, 150, 110]
```

- [ ] **Step 2: 실패 확인** — `python3 -m pytest experiments/market_signals/tests/test_ed_detect.py -q` → `ModuleNotFoundError` FAIL 확인

- [ ] **Step 3: 구현**

```python
"""croesus/events/detectors.py의 가격 기반 규칙을 과거 전체에 소급 적용.

프로덕션 events 테이블은 5일치뿐(역사화 갭)이라, 동일 규칙(abnormal_return 3σ,
abnormal_volume z≥2 상방)을 30년 이력에 벡터화 재계산한다. 두 규칙 모두 trailing
윈도만 쓰므로 look-ahead가 없다. 파라미터는 프로덕션과 동일하게 고정(튜닝 금지).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RETURN_WINDOW = 63
RETURN_SIGMA_MULT = 3.0
VOLUME_WINDOW = 21
VOLUME_Z_THRESHOLD = 2.0

COLUMNS = ["date", "pos", "event_type", "direction", "magnitude"]


def scan_asset_events(prices: pd.DataFrame) -> pd.DataFrame:
    """One asset's full history -> events with integer row positions.

    ``pos`` indexes rows of ``prices`` so CAR slicing can use integer offsets
    into the asset's own return series.
    """
    df = prices.sort_index()
    close = pd.to_numeric(df["close"], errors="coerce")
    volume = pd.to_numeric(df["volume"], errors="coerce")
    ret = close.pct_change()
    ret = ret.where(np.isfinite(ret))

    sigma = ret.rolling(RETURN_WINDOW).std().shift(1)
    mult = ret / sigma
    r_hit = ((mult.abs() >= RETURN_SIGMA_MULT) & (sigma > 0)).fillna(False)

    vmean = volume.rolling(VOLUME_WINDOW).mean().shift(1)
    vstd = volume.rolling(VOLUME_WINDOW).std().shift(1)
    z = (volume - vmean) / vstd
    v_hit = ((z >= VOLUME_Z_THRESHOLD) & (vstd > 0)).fillna(False)

    rows = []
    for i in np.flatnonzero(r_hit.to_numpy()):
        rows.append({"date": df.index[i], "pos": int(i),
                     "event_type": "abnormal_return",
                     "direction": "up" if ret.iloc[i] > 0 else "down",
                     "magnitude": float(mult.iloc[i])})
    for i in np.flatnonzero(v_hit.to_numpy()):
        rows.append({"date": df.index[i], "pos": int(i),
                     "event_type": "abnormal_volume", "direction": "up",
                     "magnitude": float(z.iloc[i])})
    return pd.DataFrame(rows, columns=COLUMNS)


def dedupe_events(events: pd.DataFrame, min_gap: int = 21) -> pd.DataFrame:
    """Per (asset_id, event_type): drop events within min_gap rows of the last kept one."""
    keep: list[int] = []
    for _, grp in events.sort_values("pos").groupby(["asset_id", "event_type"], sort=False):
        last = -(10 ** 9)
        for idx, pos in zip(grp.index, grp["pos"]):
            if pos - last >= min_gap:
                keep.append(idx)
                last = pos
    return events.loc[sorted(keep)].reset_index(drop=True)
```

- [ ] **Step 4: 통과 확인** — 같은 명령 → 4 passed
- [ ] **Step 5: Commit** — `🧪 test + ✨ feat: event_drift 소급 탐지 (detect)` 형식으로 `git add experiments/market_signals/event_drift/ experiments/market_signals/tests/test_ed_detect.py && git commit -m "✨ feat: 로드맵 ③ 이벤트 소급 탐지 모듈"`

---

### Task 2: 시장조정 CAR 경로 (`car.py`)

**Files:**
- Create: `experiments/market_signals/event_drift/car.py`
- Test: `experiments/market_signals/tests/test_ed_car.py`

**Interfaces:**
- Consumes: Task 1의 events(`asset_id, date, pos, ...`, RangeIndex 0..n-1 필수).
- Produces: `asset_excess(prices: dict[str, pd.DataFrame], market: pd.Series) -> dict[str, pd.Series]` — 자산별 (일수익률 − 시장수익률), **인덱스는 해당 자산 prices 프레임과 동일**(pos 정렬 유지).
- Produces: `event_car_paths(excess_by_asset: dict[str, pd.Series], events: pd.DataFrame, horizon: int = 60) -> pd.DataFrame` — 행=이벤트(events.index), 열=1..horizon, 값=CAR(T+1..T+k); 잔여 이력이 짧으면 NaN 패딩, 결측 초과수익일은 0 기여(nancumsum).

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""로드맵 ③ — CAR 경로 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.car import asset_excess, event_car_paths


def _mk():
    idx = pd.bdate_range("2020-01-01", periods=6)
    px = pd.DataFrame({"close": [100, 110, 121, 133.1, 146.41, 161.05],
                       "volume": [1e6] * 6}, index=idx)  # 매일 +10%
    mkt = pd.Series(0.02, index=idx)
    return {"A": px}, mkt


def test_asset_excess_alignment():
    prices, mkt = _mk()
    ex = asset_excess(prices, mkt)["A"]
    assert len(ex) == 6 and np.isnan(ex.iloc[0])  # 첫날 수익률 NaN
    assert abs(ex.iloc[1] - 0.08) < 1e-9  # 0.10 - 0.02


def test_event_car_paths_cumulative_and_padding():
    prices, mkt = _mk()
    ex = asset_excess(prices, mkt)
    ev = pd.DataFrame({"asset_id": ["A"], "date": [prices["A"].index[2]], "pos": [2],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    car = event_car_paths(ex, ev, horizon=5)
    # T+1..T+3 = rows 3,4,5의 excess ≈ 0.08씩 → CAR 0.08, 0.16, 0.24; T+4,5는 NaN
    assert abs(car.loc[0, 1] - 0.08) < 1e-6
    assert abs(car.loc[0, 3] - 0.24) < 1e-6
    assert np.isnan(car.loc[0, 4]) and np.isnan(car.loc[0, 5])


def test_event_on_last_row_all_nan():
    prices, mkt = _mk()
    ex = asset_excess(prices, mkt)
    ev = pd.DataFrame({"asset_id": ["A"], "date": [prices["A"].index[-1]], "pos": [5],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    car = event_car_paths(ex, ev, horizon=3)
    assert car.loc[0].isna().all()
```

- [ ] **Step 2: 실패 확인** — `python3 -m pytest experiments/market_signals/tests/test_ed_car.py -q` → ImportError FAIL
- [ ] **Step 3: 구현**

```python
"""이벤트별 시장조정 누적초과수익(CAR) 경로."""
from __future__ import annotations

import numpy as np
import pandas as pd


def asset_excess(prices: dict[str, pd.DataFrame], market: pd.Series) -> dict[str, pd.Series]:
    """Per-asset daily excess return, index-aligned to each asset's price frame."""
    out: dict[str, pd.Series] = {}
    for aid, df in prices.items():
        ret = pd.to_numeric(df["close"], errors="coerce").pct_change()
        ret = ret.where(np.isfinite(ret))
        out[aid] = ret - market.reindex(ret.index)
    return out


def event_car_paths(excess_by_asset: dict[str, pd.Series], events: pd.DataFrame,
                    horizon: int = 60) -> pd.DataFrame:
    """CAR(T+1..T+k) per event. Rows follow events.index; short tails are NaN-padded.

    Missing excess days inside a live path contribute 0 (nancumsum) — delistings
    truncate the path instead of poisoning it.
    """
    out = np.full((len(events), horizon), np.nan)
    row_of = {idx: r for r, idx in enumerate(events.index)}
    for aid, grp in events.groupby("asset_id"):
        ex = excess_by_asset[aid].to_numpy(dtype=float)
        for idx, pos in zip(grp.index, grp["pos"]):
            fwd = ex[int(pos) + 1: int(pos) + 1 + horizon]
            if len(fwd) == 0 or np.all(np.isnan(fwd)):
                continue
            out[row_of[idx], : len(fwd)] = np.nancumsum(np.nan_to_num(fwd))
    return pd.DataFrame(out, index=events.index, columns=list(range(1, horizon + 1)))
```

- [ ] **Step 4: 통과 확인** — 3 passed
- [ ] **Step 5: Commit** — `✨ feat: 로드맵 ③ 시장조정 CAR 경로 모듈`

---

### Task 3: 날짜 군집 CAAR + placebo (`caar.py`)

**Files:**
- Create: `experiments/market_signals/event_drift/caar.py`
- Test: `experiments/market_signals/tests/test_ed_caar.py`

**Interfaces:**
- Consumes: Task 2의 `event_car_paths` 출력(car DataFrame), events의 `date` 컬럼, Task 1 events 스키마.
- Produces: `caar_table(car: pd.DataFrame, event_dates: pd.Series) -> pd.DataFrame` — 컬럼 `h, caar, t, n_dates, n_events`; 같은 날짜 이벤트 CAR을 날짜 평균으로 축약 후 Newey-West(lags=h) t.
- Produces: `placebo_events(events: pd.DataFrame, prices: dict[str, pd.DataFrame], seed: int = 42, warmup: int = 64) -> pd.DataFrame` — 자산별 이벤트 개수 보존, pos를 [warmup, n−2]에서 균등 추출, date 재계산; RangeIndex 반환.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""로드맵 ③ — CAAR 추론 + placebo 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.caar import caar_table, placebo_events


def test_caar_clusters_same_day_events():
    # h=1 CAR: 날짜 d1에 2건(0.10, 0.30), d2에 1건(0.20) → 날짜 평균 [0.20, 0.20]
    car = pd.DataFrame({1: [0.10, 0.30, 0.20]}, index=[0, 1, 2])
    dates = pd.Series(pd.to_datetime(["2020-01-05", "2020-01-05", "2020-02-01"]),
                      index=[0, 1, 2])
    tbl = caar_table(car, dates)
    row = tbl[tbl["h"] == 1].iloc[0]
    assert abs(row["caar"] - 0.20) < 1e-12
    assert row["n_dates"] == 2 and row["n_events"] == 3


def test_caar_handles_all_nan_horizon():
    car = pd.DataFrame({1: [0.1], 2: [np.nan]}, index=[0])
    dates = pd.Series(pd.to_datetime(["2020-01-05"]), index=[0])
    tbl = caar_table(car, dates)
    assert tbl[tbl["h"] == 2].iloc[0]["n_events"] == 0


def test_placebo_preserves_counts_and_is_reproducible():
    idx = pd.bdate_range("2019-01-01", periods=300)
    prices = {"A": pd.DataFrame({"close": np.linspace(50, 60, 300),
                                 "volume": 1e6}, index=idx)}
    ev = pd.DataFrame({"asset_id": ["A"] * 3,
                       "date": [idx[100], idx[150], idx[200]],
                       "pos": [100, 150, 200],
                       "event_type": ["abnormal_return"] * 3,
                       "direction": ["up", "down", "up"],
                       "magnitude": [3.5, -4.0, 3.1]})
    p1 = placebo_events(ev, prices, seed=7)
    p2 = placebo_events(ev, prices, seed=7)
    assert len(p1) == 3
    assert (p1["pos"] >= 64).all() and (p1["pos"] <= 298).all()
    assert list(p1["pos"]) == list(p2["pos"])  # 재현성
    assert (p1["date"].to_numpy() == idx[p1["pos"]].to_numpy()).all()
```

- [ ] **Step 2: 실패 확인** — ImportError FAIL
- [ ] **Step 3: 구현**

```python
"""날짜 군집(Fama-MacBeth식) CAAR 추론 + 무작위 날짜 placebo."""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.market_signals.cross_sectional.stats import newey_west_se


def caar_table(car: pd.DataFrame, event_dates: pd.Series) -> pd.DataFrame:
    """CAAR(h) with same-day events collapsed to one date-level observation.

    Overlapping post-event windows across nearby dates leave serial correlation
    in the date series — Newey-West with lags=h covers exactly that overlap.
    """
    rows = []
    for h in car.columns:
        s = car[h].dropna()
        if len(s) == 0:
            rows.append({"h": int(h), "caar": np.nan, "t": np.nan,
                         "n_dates": 0, "n_events": 0})
            continue
        by_date = s.groupby(event_dates.loc[s.index]).mean().sort_index()
        se = newey_west_se(by_date.to_numpy(), lags=int(h))
        mean = float(by_date.mean())
        t = mean / se if np.isfinite(se) and se > 0 else np.nan
        rows.append({"h": int(h), "caar": mean, "t": float(t),
                     "n_dates": int(len(by_date)), "n_events": int(len(s))})
    return pd.DataFrame(rows)


def placebo_events(events: pd.DataFrame, prices: dict[str, pd.DataFrame],
                   seed: int = 42, warmup: int = 64) -> pd.DataFrame:
    """Same per-asset event counts, uniformly random positions — the null."""
    rng = np.random.RandomState(seed)
    out = events.copy().reset_index(drop=True)
    for aid, grp in out.groupby("asset_id"):
        n = len(prices[aid])
        pos = rng.randint(warmup, n - 1, size=len(grp))  # [warmup, n-2]
        out.loc[grp.index, "pos"] = pos
        out.loc[grp.index, "date"] = prices[aid].index[pos]
    return out
```

- [ ] **Step 4: 통과 확인** — 3 passed
- [ ] **Step 5: Commit** — `✨ feat: 로드맵 ③ 날짜 군집 CAAR + placebo`

---

### Task 4: calendar-time 이벤트 포트폴리오 (`portfolio.py`)

**Files:**
- Create: `experiments/market_signals/event_drift/portfolio.py`
- Test: `experiments/market_signals/tests/test_ed_portfolio.py`

**Interfaces:**
- Consumes: Task 2의 `asset_excess` 출력, Task 1 events 스키마.
- Produces: `event_portfolio_returns(excess_by_asset: dict[str, pd.Series], events: pd.DataFrame, hold: int = 21, cost_bps: float = 0.0) -> tuple[pd.Series, float]` — (일별 순수익률, 평균 일회전율). 이벤트 후 [T+1, T+hold] 동안 방향 부호로 보유, 일별 총노출 1로 정규화, 비용=|Δw|합×bps.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""로드맵 ③ — calendar-time 이벤트 포트폴리오 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.event_drift.portfolio import event_portfolio_returns


def _ex(vals, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(vals))
    return pd.Series(vals, index=idx)


def test_single_up_event_holds_next_days():
    ex = {"A": _ex([np.nan, 0.01, 0.02, 0.03, 0.04])}
    ev = pd.DataFrame({"asset_id": ["A"], "date": [ex["A"].index[1]], "pos": [1],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    ret, to = event_portfolio_returns(ex, ev, hold=2, cost_bps=0.0)
    # 보유일: rows 2,3 (T+1..T+2), weight=1 → 수익 0.02, 0.03; 그 외 0
    assert abs(ret.loc[ex["A"].index[2]] - 0.02) < 1e-12
    assert abs(ret.loc[ex["A"].index[3]] - 0.03) < 1e-12
    assert abs(ret.loc[ex["A"].index[4]]) < 1e-12


def test_down_event_is_short():
    ex = {"A": _ex([np.nan, 0.01, 0.02])}
    ev = pd.DataFrame({"asset_id": ["A"], "date": [ex["A"].index[1]], "pos": [1],
                       "event_type": ["abnormal_return"], "direction": ["down"],
                       "magnitude": [-4.0]})
    ret, _ = event_portfolio_returns(ex, ev, hold=1)
    assert abs(ret.loc[ex["A"].index[2]] + 0.02) < 1e-12


def test_cost_reduces_return_on_rebalance_days():
    ex = {"A": _ex([np.nan, 0.01, 0.0, 0.0])}
    ev = pd.DataFrame({"asset_id": ["A"], "date": [ex["A"].index[1]], "pos": [1],
                       "event_type": ["abnormal_return"], "direction": ["up"],
                       "magnitude": [4.0]})
    r0, _ = event_portfolio_returns(ex, ev, hold=1, cost_bps=0.0)
    r10, to = event_portfolio_returns(ex, ev, hold=1, cost_bps=10.0)
    # 진입일(row 2)과 청산일(row 3)에 |Δw|=1 → 각 10bps 비용
    assert abs((r0 - r10).loc[ex["A"].index[2]] - 0.001) < 1e-12
    assert abs((r0 - r10).loc[ex["A"].index[3]] - 0.001) < 1e-12
    assert to > 0
```

- [ ] **Step 2: 실패 확인** — ImportError FAIL
- [ ] **Step 3: 구현**

```python
"""Calendar-time 이벤트 포트폴리오 — 이벤트 방향으로 [T+1, T+hold] 보유."""
from __future__ import annotations

import numpy as np
import pandas as pd


def event_portfolio_returns(excess_by_asset: dict[str, pd.Series], events: pd.DataFrame,
                            hold: int = 21, cost_bps: float = 0.0) -> tuple[pd.Series, float]:
    """Daily net returns of a signed, gross-1-normalized event book + avg daily turnover."""
    sig: dict[str, pd.Series] = {}
    for aid, grp in events.groupby("asset_id"):
        ex = excess_by_asset[aid]
        s = np.zeros(len(ex))
        for pos, d in zip(grp["pos"], grp["direction"]):
            sgn = 1.0 if d == "up" else -1.0
            s[int(pos) + 1: int(pos) + 1 + hold] += sgn
        sig[aid] = pd.Series(s, index=ex.index)
    signal = pd.DataFrame(sig).fillna(0.0).sort_index()
    gross = signal.abs().sum(axis=1)
    weights = signal.div(gross.where(gross > 0), axis=0).fillna(0.0)
    excess = pd.DataFrame({aid: excess_by_asset[aid] for aid in signal.columns})
    excess = excess.reindex(weights.index)
    ret = (weights * excess).sum(axis=1)  # NaN excess → 그 종목 기여 0
    turnover = weights.diff().abs().sum(axis=1)
    if len(turnover) > 0:
        turnover.iloc[0] = float(weights.iloc[0].abs().sum())
    net = (ret - turnover * cost_bps / 1e4).rename("event_port")
    return net, float(turnover.mean())
```

- [ ] **Step 4: 통과 확인** — 3 passed
- [ ] **Step 5: Commit** — `✨ feat: 로드맵 ③ calendar-time 이벤트 포트폴리오`

---

### Task 5: orchestration (`run.py`) + 스모크

**Files:**
- Create: `experiments/market_signals/event_drift/run.py`
- Test: 스모크는 env 축소 실행으로 대체(단위 테스트 없음 — 순수 조립 코드)

**Interfaces:**
- Consumes: Tasks 1–4 전부 + `load_long_history`, `equal_weight_returns`, `perf_summary`, `RESULTS_DIR`.
- Produces: `results/event_drift/`에 `events_summary.csv`, `caar_<type>_<dir>.csv`(placebo 컬럼 병합), `magnitude_quintiles.csv`, `portfolio.csv`.

- [ ] **Step 1: 구현**

```python
"""로드맵 ③ orchestration — 이벤트 소급 탐지 + CAAR + placebo + 포트폴리오.

Run from repo root:
  python3 -m experiments.market_signals.event_drift.run
Env:
  ED_MAX_ASSETS=25    자산 수 제한(스모크용; 0=전체)
  ED_START_YEAR=1990  이력 시작 연도
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.history import load_long_history
from experiments.market_signals.cross_sectional.portfolio import perf_summary
from experiments.market_signals.event_drift.caar import caar_table, placebo_events
from experiments.market_signals.event_drift.car import asset_excess, event_car_paths
from experiments.market_signals.event_drift.detect import dedupe_events, scan_asset_events
from experiments.market_signals.event_drift.portfolio import event_portfolio_returns
from experiments.market_signals.vol_targeting.data import equal_weight_returns

OUT = RESULTS_DIR / "event_drift"
HORIZON = 60
MIN_GAP = 21
GROUPS = [("abnormal_return", "up"), ("abnormal_return", "down"), ("abnormal_volume", "up")]
PRINT_H = [1, 2, 3, 5, 10, 21, 40, 60]
HOLDS = [5, 21]
COSTS_BPS = [0.0, 10.0]
QUANTILE_H = [5, 21, 60]
START_YEAR = int(os.environ.get("ED_START_YEAR", "1990"))
MAX_ASSETS = int(os.environ.get("ED_MAX_ASSETS", "0"))


def _load() -> tuple[dict[str, pd.DataFrame], pd.Series]:
    prices = load_long_history(start_year=START_YEAR)
    if MAX_ASSETS:
        prices = {k: prices[k] for k in sorted(prices)[:MAX_ASSETS]}
    market = equal_weight_returns(prices)
    return prices, market


def _scan_all(prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = []
    for aid, df in prices.items():
        ev = scan_asset_events(df)
        if len(ev):
            ev.insert(0, "asset_id", aid)
            frames.append(ev)
    return pd.concat(frames, ignore_index=True)


def _caar_with_placebo(events, excess, prices) -> pd.DataFrame:
    car = event_car_paths(excess, events, HORIZON)
    tbl = caar_table(car, events["date"])
    pl = placebo_events(events, prices)
    pl_car = event_car_paths(excess, pl, HORIZON)
    pl_tbl = caar_table(pl_car, pl["date"])[["h", "caar", "t"]]
    pl_tbl.columns = ["h", "placebo_caar", "placebo_t"]
    return tbl.merge(pl_tbl, on="h")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prices, market = _load()
    excess = asset_excess(prices, market)
    print(f"[ed] {len(prices)} assets, market {market.index[0].date()}..{market.index[-1].date()}",
          flush=True)

    raw = _scan_all(prices)
    events = dedupe_events(raw, MIN_GAP)
    summary = (events.groupby(["event_type", "direction"]).size().rename("n_dedup")
               .to_frame().join(raw.groupby(["event_type", "direction"]).size().rename("n_raw")))
    summary.to_csv(OUT / "events_summary.csv")
    print(f"[ed] events (raw -> dedup {MIN_GAP}d):\n{summary.to_string()}", flush=True)

    for etype, edir in GROUPS:
        grp = events[(events["event_type"] == etype)
                     & (events["direction"] == edir)].reset_index(drop=True)
        tbl = _caar_with_placebo(grp, excess, prices)
        tbl.to_csv(OUT / f"caar_{etype}_{edir}.csv", index=False)
        show = tbl[tbl["h"].isin(PRINT_H)]
        print(f"[ed] CAAR {etype}/{edir} (n={len(grp)}):\n"
              f"{show.round(4).to_string(index=False)}", flush=True)

    # 서프라이즈 크기 분위: |magnitude| 5분위별 CAAR(h) — 진짜 drift면 단조.
    q_rows = []
    for edir in ["up", "down"]:
        grp = events[(events["event_type"] == "abnormal_return")
                     & (events["direction"] == edir)].reset_index(drop=True)
        quintile = pd.qcut(grp["magnitude"].abs(), 5, labels=False) + 1
        car = event_car_paths(excess, grp, HORIZON)
        for q in range(1, 6):
            sub = car[quintile == q]
            dates = grp.loc[sub.index, "date"]
            tbl = caar_table(sub[QUANTILE_H], dates)
            for _, r in tbl.iterrows():
                q_rows.append({"direction": edir, "quintile": q, **r.to_dict()})
    qdf = pd.DataFrame(q_rows)
    qdf.to_csv(OUT / "magnitude_quintiles.csv", index=False)
    print(f"[ed] magnitude quintiles (h=21):\n"
          f"{qdf[qdf['h'] == 21].round(4).to_string(index=False)}", flush=True)

    # calendar-time 포트폴리오 (abnormal_return, 방향 부호).
    ar = events[events["event_type"] == "abnormal_return"].reset_index(drop=True)
    years = (market.index[-1] - market.index[0]).days / 365.25
    ppy = len(market) / years
    p_rows = []
    for hold in HOLDS:
        for cost in COSTS_BPS:
            ret, to = event_portfolio_returns(excess, ar, hold, cost)
            active = ret[ret != 0.0]
            p = perf_summary(ret, ppy)
            p_rows.append({"hold": hold, "cost_bps": cost, "sharpe": p["sharpe"],
                           "ann_ret": p["mean"] * ppy, "maxdd": p["maxdd"],
                           "avg_daily_turnover": to,
                           "pct_days_active": len(active) / max(len(ret), 1)})
    pdf = pd.DataFrame(p_rows)
    pdf.to_csv(OUT / "portfolio.csv", index=False)
    print(f"[ed] event portfolio (abnormal_return, signed):\n"
          f"{pdf.round(4).to_string(index=False)}", flush=True)
    print(f"[ed] wrote results to {OUT}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 스모크 실행** — `ED_MAX_ASSETS=25 ED_START_YEAR=2015 python3 -m experiments.market_signals.event_drift.run` → 크래시 없이 전 섹션 출력, `results/event_drift/*.csv` 생성 확인
- [ ] **Step 3: 전체 테스트** — `python3 -m pytest experiments/market_signals/tests/ -q` → 전부 통과
- [ ] **Step 4: Commit** — `✨ feat: 로드맵 ③ orchestration (run)`

---

### Task 6: 전체 실행 (백그라운드)

- [ ] **Step 1: 전체 실행** — `python3 -m experiments.market_signals.event_drift.run > $CLAUDE_JOB_DIR/tmp/ed_run.log 2>&1` 를 run_in_background로 실행 (예상 수 분: 탐지·CAR은 numpy 슬라이싱이라 빠름, placebo 포함 이벤트 ~10만 건)
- [ ] **Step 2: 로그 검증** — 이벤트 수가 사전 추정(abnormal_return 57k raw)과 자릿수 일치, CAAR·placebo·quintile·portfolio 표 확인

---

### Task 7: FINDINGS.md + README.md

**Files:**
- Create: `experiments/market_signals/event_drift/FINDINGS.md`
- Create: `experiments/market_signals/event_drift/README.md`

- [ ] **Step 1: FINDINGS.md 작성** — 구조는 ②와 동일: 한 줄 결론 → 그룹별 CAAR 표(placebo 병기) → 크기 분위 단조성 → 포트폴리오(비용 민감도) → 정직한 관찰 → 한계(섹터 중립화 생략, survivorship, 이벤트 겹침 잔존, disclosures/컨센서스 부재로 진짜 PEAD 아님을 명시) → ①·②와의 관계
- [ ] **Step 2: README.md 작성** — 실행법, 방법(탐지 규칙이 프로덕션과 동일함 + 소급 재계산의 point-in-time 안전성), 산출물, 한계
- [ ] **Step 3: Commit** — `📝 docs: 로드맵 ③ findings — 이벤트 후 표류`

---

### Task 8: 로드맵 갱신 + 마무리

- [ ] **Step 1: `experiments/RESEARCH_ROADMAP.md` 갱신** — ③ 상태 `**DONE** (2026-07-02)` + 결과 요약 blockquote(핵심 수치, 3b 변형 사유: events 5일치·disclosures 0행), 3c(컨센서스 수집)는 별도 태스크로 남김
- [ ] **Step 2: 전체 테스트 최종 확인** — `python3 -m pytest experiments/market_signals/tests/ -q`
- [ ] **Step 3: Commit + push** — `📝 docs: 로드맵 ③ 완료 표기` 후 `git push origin worktree-experiments-market-signal` (기존 PR #58에 반영됨)
