# Cross-Sectional Information Coefficient (로드맵 ①) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Croesus가 계산하는 가격 기반 공통 팩터가 실제로 forward 종목 수익률을 예측하는지 cross-sectional IC와 분위 롱숏으로 검증한다.

**Architecture:** `prices_daily`(2009–2026, 전체 히스토리)에서 Croesus `factors/common.py` 정의를 그대로 복제해 월별 rebalance 시점마다 팩터 패널을 구성한다. 시점별 Spearman IC(→ 평균 IC, Newey-West t-stat, IC IR, hit rate, decay)와 5분위 Q5−Q1 롱숏(누적수익, Sharpe, 비용 민감도)을 계산한다. 순수 계산 모듈은 TDD, 데이터 조립/오케스트레이션은 smoke-run.

**Tech Stack:** Python, duckdb(read-only), pandas, numpy, scipy(spearman), pytest.

## Global Constraints

- **자립형 모듈**: `experiments/market_signals/cross_sectional/` 아래. 메인 `croesus/`는 읽기만, 통합 안 함.
- **DB 재사용 & read-only**: 실데이터는 메인 체크아웃 `storage/croesus.duckdb`. 워크트리 DB는 stale이므로 소스 DB 경로를 별도 해석(`CROESUS_SOURCE_DB` env → 메인 체크아웃 → 워크트리 순). 소스 DB는 **read_only=True**로만 연다. 두 번째 DB 만들지 말 것.
- **무거운 deps 없음**: duckdb/pandas/numpy/scipy만. 루트 `pyproject.toml` 손대지 말 것.
- **산출물**: `experiments/market_signals/results/cross_sectional/`(gitignore) + `experiments/market_signals/cross_sectional/FINDINGS.md`.
- **가격**: `adjusted_close` 사용(팩터·forward 수익률 모두). Croesus는 `close`를 쓰지만 배당조정 없는 total-return 왜곡을 피하려 adjusted 사용, FINDINGS에 편차 명시.
- **팩터 정의**(croesus/factors/common.py 복제, 히스토리 슬라이스 `[:as_of]` 기준):
  - `momentum_1m/3m/6m` = `close[-1]/close[-1-{21,63,126}] - 1`
  - `volatility_3m` = 최근 63일 일간수익률 std
  - `liquidity_1m` = 최근 21일 `(close*volume)` 평균
  - `above_200d_ma` = `close[-1] > mean(close[-200:])` → 1/0
  - `beta_1y` = 최근 252일 종목 vs 시장(등가중 유니버스 일간수익률) 회귀 베타
  - 최소 200일 히스토리 없으면 그 시점 해당 종목 제외.
- **자기기만 방지**: 다중검정(팩터×horizon 병기 우연기대치/순열검정), look-ahead 금지(시점 t 팩터는 `[:t]`만, forward 수익률은 `[t:t+h]`), survivorship 상방편향 명시, 소표본 신뢰구간 보고, 롱숏 비용 bps 민감도 보고.

---

## File Structure

```
experiments/market_signals/cross_sectional/
  __init__.py
  source.py       # 소스 DB 경로 해석 + read-only 커넥션
  universe.py     # load_universe_prices() -> {asset_id: DataFrame[date,close,volume]}, load_sectors()
  factors.py      # compute_factors_asof(hist, as_of) -> dict[factor_name,value]  (순수, TDD)
  returns.py      # forward_returns(series, as_of, horizons) -> dict[h, ret]        (순수, TDD)
  panel.py        # build_panel(prices, rebalance_dates, horizons) -> long DataFrame (통합)
  ic.py           # spearman_ic, summarize_ic(mean/NW t/IR/hit), ic_decay             (순수, TDD)
  stats.py        # newey_west_se, permutation_ic_null                                (순수, TDD)
  portfolio.py    # quintile_long_short, turnover, apply_costs, perf_summary          (순수, TDD)
  run.py          # 오케스트레이션 → results CSV                                       (smoke)
  README.md
tests/experiments/market_signals/cross_sectional/
  test_factors.py test_returns.py test_ic.py test_stats.py test_portfolio.py
```

---

## Task 1: 소스 DB 해석 + 유니버스 로더

**Files:**
- Create: `experiments/market_signals/cross_sectional/__init__.py` (빈 파일)
- Create: `experiments/market_signals/cross_sectional/source.py`
- Create: `experiments/market_signals/cross_sectional/universe.py`

**Interfaces:**
- Produces: `source_db_path() -> Path`, `connect_source() -> duckdb.DuckDBPyConnection` (read-only)
- Produces: `load_universe_prices(equities_only=True, min_rows=200) -> dict[str, pd.DataFrame]` (index=date(datetime64), cols `close`,`volume`; close=adjusted_close), `load_sectors() -> dict[str,str]`

- [ ] **Step 1: source.py**

```python
import os
from pathlib import Path
import duckdb
from experiments.market_signals.common.config import REPO_ROOT

def source_db_path() -> Path:
    env = os.environ.get("CROESUS_SOURCE_DB")
    if env:
        return Path(env)
    # 워크트리면 REPO_ROOT = .../croesus/.claude/worktrees/<wt>; 메인 체크아웃은 3단계 위
    for cand in (REPO_ROOT.parents[2] / "storage" / "croesus.duckdb",
                 REPO_ROOT / "storage" / "croesus.duckdb"):
        if cand.exists() and cand.stat().st_size > 10_000_000:
            return cand
    return REPO_ROOT / "storage" / "croesus.duckdb"

def connect_source() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(source_db_path()), read_only=True)
```

- [ ] **Step 2: universe.py**

```python
import pandas as pd
from experiments.market_signals.cross_sectional.source import connect_source

def load_universe_prices(equities_only: bool = True, min_rows: int = 200) -> dict[str, pd.DataFrame]:
    con = connect_source()
    try:
        where = "a.asset_type='equity'" if equities_only else "1=1"
        df = con.execute(f"""
            SELECT p.asset_id, p.date, p.adjusted_close AS close, p.volume
            FROM prices_daily p JOIN assets a ON a.asset_id=p.asset_id
            WHERE {where} AND p.adjusted_close IS NOT NULL AND p.adjusted_close>0
            ORDER BY p.asset_id, p.date
        """).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    out = {}
    for aid, g in df.groupby("asset_id"):
        if len(g) >= min_rows:
            out[aid] = g.set_index("date")[["close", "volume"]].sort_index()
    return out

def load_sectors() -> dict[str, str]:
    con = connect_source()
    try:
        rows = con.execute("SELECT asset_id, sector FROM assets WHERE sector IS NOT NULL").fetchall()
    finally:
        con.close()
    return {a: s for a, s in rows}
```

- [ ] **Step 3: smoke**

Run: `python -c "from experiments.market_signals.cross_sectional.universe import load_universe_prices as f; d=f(); print(len(d), sum(len(v) for v in d.values()))"`
Expected: ~500 assets, ~1.2M rows 출력(에러 없이).

- [ ] **Step 4: Commit**

```bash
git add experiments/market_signals/cross_sectional/__init__.py experiments/market_signals/cross_sectional/source.py experiments/market_signals/cross_sectional/universe.py
git commit -m "✨ feat: cross-sectional universe loader (read-only source DB)"
```

---

## Task 2: 팩터 계산 (순수, TDD)

**Files:**
- Create: `experiments/market_signals/cross_sectional/factors.py`
- Test: `tests/experiments/market_signals/cross_sectional/test_factors.py`

**Interfaces:**
- Produces: `FACTOR_NAMES: tuple[str,...]`; `compute_factors_asof(hist: pd.DataFrame, as_of, market_ret: pd.Series | None = None) -> dict[str,float]` — `hist`는 index=date, cols `close`,`volume`; `as_of` 이하로 슬라이스 후 계산. beta는 `market_ret`(index=date 일간수익률) 있을 때만.

- [ ] **Step 1: 실패하는 테스트**

```python
import numpy as np, pandas as pd
from experiments.market_signals.cross_sectional.factors import compute_factors_asof

def _hist(n=260, start=100.0, step=1.0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(start + step*np.arange(n), index=idx)
    vol = pd.Series(1000.0, index=idx)
    return pd.DataFrame({"close": close, "volume": vol})

def test_momentum_matches_definition():
    h = _hist()
    f = compute_factors_asof(h, h.index[-1])
    c = h["close"]
    assert abs(f["momentum_1m"] - (c.iloc[-1]/c.iloc[-1-21]-1)) < 1e-12
    assert abs(f["momentum_3m"] - (c.iloc[-1]/c.iloc[-1-63]-1)) < 1e-12
    assert abs(f["momentum_6m"] - (c.iloc[-1]/c.iloc[-1-126]-1)) < 1e-12

def test_above_200d_ma_binary():
    h = _hist()
    f = compute_factors_asof(h, h.index[-1])
    assert f["above_200d_ma"] == 1.0  # 상승 추세

def test_asof_slices_future_out():
    h = _hist()
    mid = h.index[150]
    f = compute_factors_asof(h, mid)
    c = h["close"].loc[:mid]
    assert abs(f["momentum_1m"] - (c.iloc[-1]/c.iloc[-1-21]-1)) < 1e-12

def test_insufficient_history_returns_empty():
    h = _hist(n=50)
    assert compute_factors_asof(h, h.index[-1]) == {}
```

- [ ] **Step 2: 실패 확인** — `pytest tests/experiments/market_signals/cross_sectional/test_factors.py -v` → FAIL(import).

- [ ] **Step 3: 구현**

```python
from __future__ import annotations
import pandas as pd

FACTOR_NAMES = ("momentum_1m","momentum_3m","momentum_6m",
                "volatility_3m","liquidity_1m","above_200d_ma","beta_1y")
_BETA_WINDOW = 252

def _momentum(c: pd.Series, k: int) -> float:
    return float(c.iloc[-1]/c.iloc[-1-k]-1) if len(c) > k else float("nan")

def compute_factors_asof(hist: pd.DataFrame, as_of, market_ret: "pd.Series|None"=None) -> dict:
    d = hist.loc[:as_of].dropna(subset=["close","volume"])
    if len(d) < 200:
        return {}
    c, v = d["close"], d["volume"]
    ret = c.pct_change()
    out = {
        "momentum_1m": _momentum(c,21), "momentum_3m": _momentum(c,63),
        "momentum_6m": _momentum(c,126),
        "volatility_3m": float(ret.tail(63).std()),
        "liquidity_1m": float((c*v).tail(21).mean()),
        "above_200d_ma": 1.0 if float(c.iloc[-1]) > float(c.tail(200).mean()) else 0.0,
    }
    if market_ret is not None:
        a = ret.dropna()
        common = a.index.intersection(market_ret.index)[-_BETA_WINDOW:]
        if len(common) >= 60:
            x = market_ret.loc[common].values; y = a.loc[common].values
            var = float(((x-x.mean())**2).sum())
            if var > 0:
                out["beta_1y"] = float(((x-x.mean())*(y-y.mean())).sum()/var)
    return {k: val for k, val in out.items() if pd.notna(val)}
```

- [ ] **Step 4: 통과 확인** — `pytest tests/experiments/market_signals/cross_sectional/test_factors.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "✨ feat: as-of price-factor computation (replicates factors/common.py)"`

---

## Task 3: forward 수익률 (순수, TDD)

**Files:**
- Create: `experiments/market_signals/cross_sectional/returns.py`
- Test: `tests/experiments/market_signals/cross_sectional/test_returns.py`

**Interfaces:**
- Produces: `forward_returns(close: pd.Series, as_of, horizons: list[int]) -> dict[int,float]` — `as_of`가 series에 있어야 하며, `close[pos+h]/close[pos]-1`. `pos+h`가 범위 밖이면 그 h는 결과에서 생략.

- [ ] **Step 1: 실패 테스트**

```python
import numpy as np, pandas as pd
from experiments.market_signals.cross_sectional.returns import forward_returns

def test_forward_return_basic():
    idx = pd.bdate_range("2020-01-01", periods=200)
    close = pd.Series(100.0*(1.001**np.arange(200)), index=idx)
    as_of = idx[100]
    fr = forward_returns(close, as_of, [21,63])
    assert abs(fr[21] - (close.iloc[121]/close.iloc[100]-1)) < 1e-12
    assert abs(fr[63] - (close.iloc[163]/close.iloc[100]-1)) < 1e-12

def test_horizon_out_of_range_omitted():
    idx = pd.bdate_range("2020-01-01", periods=110)
    close = pd.Series(1.0, index=idx)
    fr = forward_returns(close, idx[100], [21,63,126])
    assert 21 not in fr and 63 not in fr and 126 not in fr  # 100+21=121>109? 실제 109가 마지막 pos
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현**

```python
import pandas as pd

def forward_returns(close: pd.Series, as_of, horizons) -> dict:
    close = close.sort_index()
    pos = close.index.get_indexer([pd.Timestamp(as_of)])[0]
    if pos < 0:
        return {}
    out = {}
    n = len(close)
    for h in horizons:
        if pos + h < n:
            out[h] = float(close.iloc[pos+h]/close.iloc[pos]-1)
    return out
```

- [ ] **Step 4: 통과 확인.** (두 번째 테스트: 110개, pos=100, 100+21=121≥110 → 생략 확인)

- [ ] **Step 5: Commit** — `git commit -m "✨ feat: forward-return helper for IC panel"`

---

## Task 4: IC 통계 (순수, TDD) — stats.py + ic.py

**Files:**
- Create: `experiments/market_signals/cross_sectional/stats.py`
- Create: `experiments/market_signals/cross_sectional/ic.py`
- Test: `tests/experiments/market_signals/cross_sectional/test_stats.py`, `test_ic.py`

**Interfaces:**
- stats.py Produces: `newey_west_se(x: np.ndarray, lags: int|None=None) -> float`(평균의 HAC 표준오차), `permutation_ic_null(values, fwd, n=1000, seed=0) -> np.ndarray`
- ic.py Produces: `spearman_ic(values: pd.Series, fwd: pd.Series) -> float`; `summarize_ic(ic_series: pd.Series) -> dict`(keys: `mean`,`std`,`t_nw`,`ir`,`hit_rate`,`n`); `ic_decay(panel, factor, horizons) -> dict[h,mean_ic]`

- [ ] **Step 1: 실패 테스트 (test_stats.py)**

```python
import numpy as np
from experiments.market_signals.cross_sectional.stats import newey_west_se, permutation_ic_null

def test_newey_west_se_positive():
    x = np.array([0.1,-0.05,0.2,0.0,0.15,-0.1,0.05,0.08])
    se = newey_west_se(x)
    assert se > 0 and np.isfinite(se)

def test_newey_west_zero_lag_matches_ols_se():
    x = np.array([1.0,2.0,3.0,4.0,5.0])
    se0 = newey_west_se(x, lags=0)
    assert abs(se0 - x.std(ddof=0)/np.sqrt(len(x))) < 1e-9

def test_permutation_null_centered_near_zero():
    rng = np.random.default_rng(0)
    v = rng.normal(size=200); f = rng.normal(size=200)
    null = permutation_ic_null(v, f, n=300, seed=1)
    assert abs(null.mean()) < 0.05 and len(null) == 300
```

- [ ] **Step 2: 실패 테스트 (test_ic.py)**

```python
import numpy as np, pandas as pd
from experiments.market_signals.cross_sectional.ic import spearman_ic, summarize_ic

def test_spearman_ic_perfect_monotone():
    v = pd.Series([1,2,3,4,5]); f = pd.Series([10,20,30,40,50])
    assert abs(spearman_ic(v,f) - 1.0) < 1e-9

def test_spearman_ic_inverse():
    v = pd.Series([1,2,3,4,5]); f = pd.Series([50,40,30,20,10])
    assert abs(spearman_ic(v,f) + 1.0) < 1e-9

def test_spearman_ic_nan_dropped():
    v = pd.Series([1,2,3,np.nan,5]); f = pd.Series([1,2,np.nan,4,5])
    assert np.isfinite(spearman_ic(v,f))

def test_summarize_ic_keys():
    s = pd.Series([0.02,0.05,-0.01,0.03,0.04,0.0,0.06])
    out = summarize_ic(s)
    assert set(["mean","std","t_nw","ir","hit_rate","n"]) <= set(out)
    assert out["n"] == 7 and 0 <= out["hit_rate"] <= 1
```

- [ ] **Step 3: 구현 stats.py**

```python
import numpy as np

def newey_west_se(x, lags=None):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    n = len(x)
    if n < 2:
        return float("nan")
    if lags is None:
        lags = int(np.floor(4*(n/100.0)**(2/9)))
    e = x - x.mean()
    gamma0 = float((e*e).sum())/n
    s = gamma0
    for l in range(1, lags+1):
        w = 1.0 - l/(lags+1)
        cov = float((e[l:]*e[:-l]).sum())/n
        s += 2*w*cov
    # 평균의 분산 ≈ 장기분산/n
    return float(np.sqrt(max(s, 0.0)/n))

def permutation_ic_null(values, fwd, n=1000, seed=0):
    from scipy.stats import spearmanr
    v = np.asarray(values, float); f = np.asarray(fwd, float)
    m = np.isfinite(v) & np.isfinite(f); v, f = v[m], f[m]
    rng = np.random.default_rng(seed)
    out = np.empty(n)
    for i in range(n):
        out[i] = spearmanr(rng.permutation(v), f).correlation
    return out
```

- [ ] **Step 4: 구현 ic.py**

```python
import numpy as np, pandas as pd
from scipy.stats import spearmanr
from experiments.market_signals.cross_sectional.stats import newey_west_se

def spearman_ic(values: pd.Series, fwd: pd.Series) -> float:
    df = pd.concat([values.rename("v"), fwd.rename("f")], axis=1).dropna()
    if len(df) < 5:
        return float("nan")
    return float(spearmanr(df["v"], df["f"]).correlation)

def summarize_ic(ic_series: pd.Series) -> dict:
    s = pd.to_numeric(ic_series, errors="coerce").dropna()
    if len(s) == 0:
        return {"mean":float("nan"),"std":float("nan"),"t_nw":float("nan"),
                "ir":float("nan"),"hit_rate":float("nan"),"n":0}
    mean = float(s.mean()); std = float(s.std(ddof=1)) if len(s)>1 else float("nan")
    se = newey_west_se(s.values)
    return {"mean":mean,"std":std,
            "t_nw": float(mean/se) if se and np.isfinite(se) and se>0 else float("nan"),
            "ir": float(mean/std) if std and std>0 else float("nan"),
            "hit_rate": float((s>0).mean()), "n": int(len(s))}

def ic_decay(panel: pd.DataFrame, factor: str, horizons) -> dict:
    out = {}
    for h in horizons:
        col = f"fwd_{h}"
        rows = panel[(panel.factor_name==factor) & panel[col].notna()]
        ics = rows.groupby("date").apply(lambda g: spearman_ic(g["value"], g[col]))
        out[h] = float(pd.to_numeric(ics, errors="coerce").dropna().mean())
    return out
```

- [ ] **Step 5: 통과 확인** — `pytest tests/experiments/market_signals/cross_sectional/test_stats.py tests/experiments/market_signals/cross_sectional/test_ic.py -v` → PASS.

- [ ] **Step 6: Commit** — `git commit -m "✨ feat: Spearman IC + Newey-West/permutation stats"`

---

## Task 5: 분위 롱숏 포트폴리오 (순수, TDD)

**Files:**
- Create: `experiments/market_signals/cross_sectional/portfolio.py`
- Test: `tests/experiments/market_signals/cross_sectional/test_portfolio.py`

**Interfaces:**
- Produces: `quintile_buckets(values: pd.Series, q: int=5) -> pd.Series`(1..q, 동점/소표본 안전); `long_short_return(values, fwd, q=5) -> float`(Q_top 평균 − Q_bottom 평균, 등가중); `turnover(prev_top:set, cur_top:set) -> float`; `perf_summary(returns: pd.Series, periods_per_year: float) -> dict`(`cum`,`sharpe`,`mean`,`vol`,`maxdd`)

- [ ] **Step 1: 실패 테스트**

```python
import numpy as np, pandas as pd
from experiments.market_signals.cross_sectional.portfolio import (
    quintile_buckets, long_short_return, turnover, perf_summary)

def test_quintile_buckets_range():
    v = pd.Series(np.arange(50, dtype=float))
    b = quintile_buckets(v, 5)
    assert set(b.unique()) <= {1,2,3,4,5} and b.notna().all()

def test_long_short_monotone_positive():
    v = pd.Series(np.arange(50, dtype=float))
    f = pd.Series(np.arange(50, dtype=float))  # 신호=미래수익 → 롱숏 +
    assert long_short_return(v, f, 5) > 0

def test_turnover_bounds():
    assert turnover({"a","b"}, {"a","b"}) == 0.0
    assert turnover({"a","b"}, {"c","d"}) == 1.0

def test_perf_summary_keys_and_maxdd_sign():
    r = pd.Series([0.01,-0.02,0.03,0.00,0.015])
    out = perf_summary(r, 12)
    assert {"cum","sharpe","mean","vol","maxdd"} <= set(out)
    assert out["maxdd"] <= 0
```

- [ ] **Step 2: 실패 확인.**

- [ ] **Step 3: 구현**

```python
import numpy as np, pandas as pd

def quintile_buckets(values: pd.Series, q: int = 5) -> pd.Series:
    v = values.dropna()
    if v.nunique() < q:
        r = v.rank(method="first")
        return np.ceil(r/len(r)*q).clip(1,q).astype(int)
    try:
        b = pd.qcut(v.rank(method="first"), q, labels=False, duplicates="drop") + 1
    except ValueError:
        r = v.rank(method="first"); b = np.ceil(r/len(r)*q).clip(1,q)
    return b.astype(int)

def long_short_return(values: pd.Series, fwd: pd.Series, q: int = 5) -> float:
    df = pd.concat([values.rename("v"), fwd.rename("f")], axis=1).dropna()
    if len(df) < q:
        return float("nan")
    b = quintile_buckets(df["v"], q)
    top = df.loc[b[b==q].index, "f"].mean()
    bot = df.loc[b[b==1].index, "f"].mean()
    return float(top - bot)

def turnover(prev_top: set, cur_top: set) -> float:
    if not cur_top:
        return 0.0
    return len(cur_top - prev_top)/len(cur_top)

def perf_summary(returns: pd.Series, periods_per_year: float) -> dict:
    r = pd.to_numeric(returns, errors="coerce").dropna()
    if len(r) == 0:
        return {"cum":0.0,"sharpe":float("nan"),"mean":float("nan"),"vol":float("nan"),"maxdd":0.0}
    cum_curve = (1+r).cumprod()
    peak = cum_curve.cummax()
    maxdd = float((cum_curve/peak - 1).min())
    mean = float(r.mean()); vol = float(r.std(ddof=1)) if len(r)>1 else float("nan")
    sharpe = float(mean/vol*np.sqrt(periods_per_year)) if vol and vol>0 else float("nan")
    return {"cum":float(cum_curve.iloc[-1]-1),"sharpe":sharpe,"mean":mean,"vol":vol,"maxdd":maxdd}
```

- [ ] **Step 4: 통과 확인.**

- [ ] **Step 5: Commit** — `git commit -m "✨ feat: quintile long-short portfolio + perf/turnover helpers"`

---

## Task 6: 패널 빌더 + 오케스트레이션 (통합, smoke)

**Files:**
- Create: `experiments/market_signals/cross_sectional/panel.py`
- Create: `experiments/market_signals/cross_sectional/run.py`
- Create: `experiments/market_signals/cross_sectional/README.md`

**Interfaces:**
- panel.py Consumes: universe.load_universe_prices, factors.compute_factors_asof, returns.forward_returns.
- panel.py Produces: `month_end_grid(all_dates, start_year=2010) -> list[Timestamp]`; `equal_weight_market_return(prices) -> pd.Series`; `build_panel(prices, rebalance_dates, horizons, market_ret) -> pd.DataFrame`(long: `date,asset_id,factor_name,value,fwd_21,fwd_63,fwd_126`).
- run.py Produces: results CSV들 + 콘솔 요약. Consumes: ic.summarize_ic/ic_decay, portfolio.long_short_return/perf_summary, stats.permutation_ic_null.

- [ ] **Step 1: panel.py**

```python
from __future__ import annotations
import pandas as pd
from experiments.market_signals.cross_sectional.factors import compute_factors_asof, FACTOR_NAMES
from experiments.market_signals.cross_sectional.returns import forward_returns

def month_end_grid(all_dates, start_year: int = 2010):
    idx = pd.DatetimeIndex(sorted(set(pd.to_datetime(all_dates))))
    idx = idx[idx.year >= start_year]
    s = pd.Series(idx, index=idx)
    return list(s.groupby([idx.year, idx.month]).last().values)

def equal_weight_market_return(prices: dict) -> pd.Series:
    rets = []
    for g in prices.values():
        rets.append(g["close"].pct_change().rename(None))
    m = pd.concat(rets, axis=1).mean(axis=1)
    m.index = pd.to_datetime(m.index)
    return m.dropna()

def build_panel(prices: dict, rebalance_dates, horizons, market_ret=None) -> pd.DataFrame:
    recs = []
    reb = [pd.Timestamp(d) for d in rebalance_dates]
    for aid, g in prices.items():
        g = g.sort_index()
        for as_of in reb:
            if as_of not in g.index:
                continue
            f = compute_factors_asof(g, as_of, market_ret)
            if not f:
                continue
            fr = forward_returns(g["close"], as_of, horizons)
            for name, val in f.items():
                rec = {"date": as_of, "asset_id": aid, "factor_name": name, "value": val}
                for h in horizons:
                    rec[f"fwd_{h}"] = fr.get(h, float("nan"))
                recs.append(rec)
    return pd.DataFrame.from_records(recs)
```

- [ ] **Step 2: run.py** (오케스트레이션 — IC 표/decay/롱숏 곡선/순열검정 → results CSV)

```python
"""로드맵 ① cross-sectional IC. 실행: python -m experiments.market_signals.cross_sectional.run"""
from pathlib import Path
import numpy as np, pandas as pd
from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.universe import load_universe_prices
from experiments.market_signals.cross_sectional.panel import (
    month_end_grid, equal_weight_market_return, build_panel)
from experiments.market_signals.cross_sectional.factors import FACTOR_NAMES
from experiments.market_signals.cross_sectional.ic import spearman_ic, summarize_ic
from experiments.market_signals.cross_sectional.portfolio import long_short_return, perf_summary
from experiments.market_signals.cross_sectional.stats import permutation_ic_null

HORIZONS = [21, 63, 126]
OUT = Path(RESULTS_DIR) / "cross_sectional"

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    prices = load_universe_prices()
    all_dates = sorted({d for g in prices.values() for d in g.index})
    grid = month_end_grid(all_dates, 2010)
    mkt = equal_weight_market_return(prices)
    panel = build_panel(prices, grid, HORIZONS, mkt)
    panel.to_parquet(OUT / "panel.parquet")

    # IC 표 (factor x horizon)
    ic_rows, ls_rows = [], []
    for fac in FACTOR_NAMES:
        sub = panel[panel.factor_name == fac]
        for h in HORIZONS:
            col = f"fwd_{h}"
            per_date = sub[sub[col].notna()].groupby("date").apply(
                lambda g: pd.Series({
                    "ic": spearman_ic(g["value"], g[col]),
                    "ls": long_short_return(g["value"], g[col], 5),
                    "n": g["value"].notna().sum(),
                }))
            if per_date.empty:
                continue
            s = summarize_ic(per_date["ic"])
            s.update({"factor": fac, "h": h, "avg_n": float(per_date["n"].mean())})
            ic_rows.append(s)
            lsr = per_date["ls"].dropna()
            perf = perf_summary(lsr, 12)
            perf.update({"factor": fac, "h": h})
            ls_rows.append(perf)
            per_date.reset_index().to_csv(OUT / f"perdate_{fac}_{h}.csv", index=False)
    pd.DataFrame(ic_rows).to_csv(OUT / "ic_summary.csv", index=False)
    pd.DataFrame(ls_rows).to_csv(OUT / "longshort_summary.csv", index=False)

    # 순열검정: 대표 horizon(63)에서 각 팩터 평균 IC vs 우연 귀무
    perm_rows = []
    for fac in FACTOR_NAMES:
        sub = panel[(panel.factor_name==fac) & panel["fwd_63"].notna()]
        obs = pd.to_numeric(
            sub.groupby("date").apply(lambda g: spearman_ic(g["value"], g["fwd_63"])),
            errors="coerce").dropna().mean()
        # 날짜별 순열 IC 평균의 분포
        null_means = []
        for _, g in list(sub.groupby("date"))[:60]:  # 대표 60개 시점
            null_means.append(permutation_ic_null(g["value"].values, g["fwd_63"].values, n=200, seed=1).mean())
        null_means = np.array([x for x in null_means if np.isfinite(x)])
        perm_rows.append({"factor": fac, "obs_mean_ic": float(obs),
                          "null_mean": float(null_means.mean()) if len(null_means) else float("nan"),
                          "null_sd": float(null_means.std()) if len(null_means) else float("nan")})
    pd.DataFrame(perm_rows).to_csv(OUT / "permutation.csv", index=False)
    print("[cross_sectional] wrote", OUT)
    print(pd.DataFrame(ic_rows)[["factor","h","mean","t_nw","ir","hit_rate","n"]].to_string(index=False))

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: smoke run**

Run: `python -m experiments.market_signals.cross_sectional.run`
Expected: `results/cross_sectional/` 아래 `ic_summary.csv`, `longshort_summary.csv`, `permutation.csv`, `panel.parquet` 생성 + IC 표 콘솔 출력. 에러 없이 완료.

- [ ] **Step 4: README.md** — 목적/실행법/산출물/한계(factor_values 미역사화로 fundamental·LLM 신호 제외, survivorship, adjusted_close 편차) 기술.

- [ ] **Step 5: Commit** — `git commit -m "✨ feat: cross-sectional IC panel builder + run harness"`

---

## Task 7: FINDINGS.md (정직한 결론)

**Files:**
- Create: `experiments/market_signals/cross_sectional/FINDINGS.md`

- [ ] **Step 1:** run.py 결과(ic_summary/longshort/permutation)를 읽고 팩터별 IC t-stat, IC decay, Q5−Q1 Sharpe/MaxDD, 비용 민감도(예: 10/20bps), 순열 귀무 대비를 표로 요약.
- [ ] **Step 2:** 자기기만 방지 체크리스트 명시: 다중검정(7팩터×3horizon=21검정, |t|>2 우연 기대), look-ahead(as-of 슬라이스), survivorship 상방편향, adjusted_close 편차, 등가중 소형주 영향.
- [ ] **Step 3:** "어떤 신호가 진짜 예측력 있나" 결론 + ④(레짐 조건부)에서 재사용할 롱숏 시계열 위치 안내.
- [ ] **Step 4: Commit** — `git commit -m "📝 docs: cross-sectional IC findings"`

---

## Self-Review 메모

- **스펙 커버리지**: 로드맵 ①의 방법 1~4(시점별 cross-section, Spearman IC+NW t+IR, 분위 롱숏+비용, 신호 비교+decay) → Task 4/5/6에 매핑. 기준선(순열검정) → run.py. 섹터 중립화는 stretch(universe.load_sectors 준비만, 본 실행 제외 — FINDINGS에 명시).
- **스코프 편차(중요)**: `factor_values` 미역사화로 fundamental/valuation/DCF/LLM 신호는 제외, 가격 기반 7팩터만. 이는 데이터 인프라 한계이며 ③/⑤가 다룰 사안 — FINDINGS·README에 기록.
- **타입 일관성**: `compute_factors_asof`/`forward_returns`/`spearman_ic`/`long_short_return` 시그니처가 panel.build_panel·run.main에서 일관되게 사용됨.
