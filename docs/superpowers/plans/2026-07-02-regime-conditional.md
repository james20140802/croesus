# 로드맵 ④ — 레짐 조건부 팩터 성과 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 팩터 롱숏 프리미엄(①의 30년 월별 시계열)이 거시 레짐(성장×인플레)에 따라 유의하게 달라지는지 검증한다.

**Architecture:** 프로덕션 레짐 엔진(`croesus/macro/indicators/{growth,inflation}.py`)은 FRED 시계열 dict에 대한 **순수 투표 함수**다. `macro_scores`는 14일치뿐(역사화 갭)이므로, FRED 공개 CSV 엔드포인트에서 원계열을 받아 **발표 시차(publication lag)를 적용한 point-in-time 뷰**로 월말마다 투표 함수를 소급 실행해 1990~2026 레짐 라벨을 만든다. ①의 `results/cross_sectional_long/perdate_<factor>_21.csv`(월별 Q5−Q1 롱숏, 1995-10~2026-04, 367개월)를 레짐별로 분해하고, 원형 시프트(circular shift) placebo로 검정한다.

**Tech Stack:** pandas/numpy(기존), urllib(FRED CSV, API 키 불요), croesus.macro.indicators 순수 함수 import(읽기 전용 재사용). 신규 의존성 0.

## Global Constraints

- 프로덕션 DB `storage/croesus.duckdb`는 **읽기 전용**으로만(sanity check 1회), 실패 시 스킵.
- 신규 heavy deps 없음 — 루트 `pyproject.toml`, `requirements.txt` 손대지 않음.
- 산출물은 `experiments/market_signals/results/regime_conditional/`(gitignore).
- 커밋은 gitmoji + Co-Authored-By 트레일러. 브랜치 `worktree-experiments-market-signal`.
- 투표 파라미터·규칙은 프로덕션 그대로(튜닝 금지). look-ahead 방지: 관측일→이용가능일 보수적 시차.

## 핵심 사전 조사 결과 (2026-07-02 확인)

- FRED CSV: `https://fred.stlouisfed.org/graph/fredgraph.csv?id=<code>` 키 없이 동작, 결측은 `"."`.
- **레벨 퇴화**: CPILFESL/PCEPILFE/CES0500000003는 *지수 레벨*이라 3개월 기울기>0이 98~99% →
  프로덕션 인플레 방향은 구조적으로 "Rising" 편향. → 라벨 2종 병행:
  - `prod` — 프로덕션 충실(레벨 그대로)
  - `yoy` — CPI/PCE/임금을 12개월 YoY(%)로 변환 후 동일 투표(경제적 의도에 부합)
- `from croesus.macro.indicators.growth import compute_growth_direction` 등 import 정상.
- ISM PMI(스크레이퍼)·MANEAPUSA(FRED 삭제)는 30년 소급 불가 → 제외(투표 함수는 결측 키 허용).

---

### Task 1: FRED 수집 + point-in-time 뷰 (`fred.py`)

**Files:**
- Create: `experiments/market_signals/regime_conditional/__init__.py` (빈 파일)
- Create: `experiments/market_signals/regime_conditional/fred.py`
- Test: `experiments/market_signals/tests/test_rc_fred.py`

**Interfaces:**
- Produces: `parse_fredgraph(text) -> pd.Series`, `fetch_series(code, cache_dir) -> pd.Series`,
  `load_all(cache_dir) -> dict[str, pd.Series]`, `as_of_view(raw, as_of, lags=LAG_DAYS) -> dict[str, pd.Series]`,
  상수 `GROWTH_SERIES`, `INFLATION_SERIES`, `ALL_SERIES`, `LAG_DAYS`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""regime_conditional.fred 테스트."""
import pandas as pd

from experiments.market_signals.regime_conditional.fred import as_of_view, parse_fredgraph


def test_parse_fredgraph_drops_dot_missing():
    text = "observation_date,XX\n2020-01-01,1.0\n2020-02-01,.\n2020-03-01,3.0\n"
    s = parse_fredgraph(text)
    assert list(s.values) == [1.0, 3.0]
    assert s.index[1] == pd.Timestamp("2020-03-01")


def test_as_of_view_applies_publication_lag():
    idx = pd.to_datetime(["2020-01-01", "2020-02-01", "2020-03-01"])
    raw = {"UNRATE": pd.Series([1.0, 2.0, 3.0], index=idx)}
    # cutoff = 3/15 - 40d = 2/4 → 1월·2월 관측만 보임
    view = as_of_view(raw, pd.Timestamp("2020-03-15"), lags={"UNRATE": 40})
    assert len(view["UNRATE"]) == 2
    assert view["UNRATE"].index[-1] == pd.Timestamp("2020-02-01")


def test_as_of_view_drops_empty_series():
    idx = pd.to_datetime(["2020-03-01"])
    raw = {"UNRATE": pd.Series([1.0], index=idx)}
    view = as_of_view(raw, pd.Timestamp("2020-01-15"), lags={"UNRATE": 40})
    assert "UNRATE" not in view
```

- [ ] **Step 2: 실패 확인** — `python3 -m pytest experiments/market_signals/tests/test_rc_fred.py -q` → ModuleNotFoundError
- [ ] **Step 3: 구현**

```python
"""FRED 공개 CSV 수집 + point-in-time(발표 시차) 뷰.

fredgraph.csv 엔드포인트는 API 키가 필요 없다. 관측일(observation_date)은
월/분기 시계열의 경우 '기간 시작일'이므로, LAG_DAYS는 관측일로부터 실제 발표
(이용 가능)일까지의 보수적 오프셋(달력일)이다: 기간 길이 + 발표 지연.
"""
from __future__ import annotations

import io
import urllib.request
from pathlib import Path

import pandas as pd

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"

GROWTH_SERIES = ["CFNAI", "UNRATE", "ICSA", "RSXFS", "INDPRO", "GDPC1"]
INFLATION_SERIES = ["CPILFESL", "PCEPILFE", "T5YIE", "DCOILWTICO", "CES0500000003"]
ALL_SERIES = GROWTH_SERIES + INFLATION_SERIES

LAG_DAYS = {
    "CFNAI": 55, "UNRATE": 40, "ICSA": 7, "RSXFS": 47, "INDPRO": 47, "GDPC1": 121,
    "CPILFESL": 45, "PCEPILFE": 60, "T5YIE": 1, "DCOILWTICO": 3, "CES0500000003": 40,
}


def parse_fredgraph(text: str) -> pd.Series:
    df = pd.read_csv(io.StringIO(text), na_values=["."])
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["value"].astype(float).dropna()


def fetch_series(code: str, cache_dir: Path) -> pd.Series:
    cache_dir.mkdir(parents=True, exist_ok=True)
    f = cache_dir / f"{code}.csv"
    if not f.exists():
        with urllib.request.urlopen(FRED_URL.format(code=code), timeout=60) as r:
            f.write_bytes(r.read())
    return parse_fredgraph(f.read_text())


def load_all(cache_dir: Path) -> dict[str, pd.Series]:
    return {c: fetch_series(c, cache_dir) for c in ALL_SERIES}


def as_of_view(raw: dict[str, pd.Series], as_of: pd.Timestamp,
               lags: dict[str, int] = LAG_DAYS) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for code, s in raw.items():
        cutoff = as_of - pd.Timedelta(days=lags.get(code, 60))
        v = s[s.index <= cutoff]
        if len(v):
            out[code] = v
    return out
```

- [ ] **Step 4: 통과 확인** — 같은 명령 → 3 passed
- [ ] **Step 5: Commit** — `🧪 test+feat: FRED point-in-time 수집 모듈 (로드맵 ④)`

### Task 2: 레짐 라벨 소급 계산 (`regimes.py`)

**Files:**
- Create: `experiments/market_signals/regime_conditional/regimes.py`
- Test: `experiments/market_signals/tests/test_rc_regimes.py`

**Interfaces:**
- Consumes: `fred.as_of_view`, croesus의 `compute_growth_direction`, `compute_inflation_direction`.
- Produces: `classify_regime(growth, inflation) -> str`, `with_yoy_inflation(raw) -> dict`,
  `monthly_regimes(raw, dates) -> pd.DataFrame[date, growth, inflation, regime, growth_conf, inflation_conf]`,
  `run_length_summary(labels) -> pd.DataFrame`, `transition_matrix(labels) -> pd.DataFrame`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""regime_conditional.regimes 테스트."""
import pandas as pd

from croesus.macro.engine import _classify_regime
from experiments.market_signals.regime_conditional.regimes import (
    classify_regime, monthly_regimes, run_length_summary, transition_matrix, with_yoy_inflation,
)


def test_classify_matches_production():
    for g in ["Expanding", "Contracting"]:
        for i in ["Rising", "Falling"]:
            assert classify_regime(g, i) == _classify_regime(g, i)


def _mk(dates, vals):
    return pd.Series(vals, index=pd.to_datetime(dates))


def test_monthly_regimes_is_point_in_time():
    # UNRATE 하락(→Expanding), CPILFESL 하락(→Falling) → Goldilocks.
    # 컷오프 이후의 급반전 관측이 새면 라벨이 뒤집힌다 → 안 뒤집혀야 PIT.
    unrate = _mk(["2020-01-01", "2020-02-01", "2020-03-01", "2020-06-01"], [5.0, 4.0, 3.0, 99.0])
    cpi = _mk(["2020-01-01", "2020-02-01", "2020-03-01", "2020-06-01"], [3.0, 2.0, 1.0, 99.0])
    raw = {"UNRATE": unrate, "CPILFESL": cpi}
    out = monthly_regimes(raw, [pd.Timestamp("2020-04-30")])
    assert out.loc[0, "regime"] == "Goldilocks"


def test_with_yoy_inflation_transforms_levels():
    idx = pd.date_range("2020-01-01", periods=25, freq="MS")
    level = pd.Series(range(100, 125), index=idx, dtype=float)
    out = with_yoy_inflation({"CPILFESL": level, "UNRATE": level})
    assert abs(out["CPILFESL"].iloc[0] - 12.0) < 1e-9  # 100→112 = +12%
    assert len(out["CPILFESL"]) == 13
    assert out["UNRATE"].equals(level)  # 성장 계열은 무변환


def test_run_lengths_and_transitions():
    labels = pd.Series(["A", "A", "B", "B", "B", "A"])
    rl = run_length_summary(labels).set_index("regime")
    assert rl.loc["A", "n_months"] == 3 and rl.loc["A", "n_runs"] == 2
    assert rl.loc["B", "avg_run_len"] == 3.0
    tm = transition_matrix(labels)
    assert tm.loc["A", "B"] == 1 and tm.loc["B", "A"] == 1
```

- [ ] **Step 2: 실패 확인** — ModuleNotFoundError
- [ ] **Step 3: 구현**

```python
"""프로덕션 투표 함수를 point-in-time 뷰로 월별 소급 실행해 레짐 라벨 생성."""
from __future__ import annotations

import pandas as pd

from croesus.macro.indicators.growth import compute_growth_direction
from croesus.macro.indicators.inflation import compute_inflation_direction
from experiments.market_signals.regime_conditional.fred import LAG_DAYS, as_of_view

YOY_SERIES = ["CPILFESL", "PCEPILFE", "CES0500000003"]


def classify_regime(growth: str, inflation: str) -> str:
    # croesus.macro.engine._classify_regime과 동일 매핑(테스트로 동치 보증)
    if growth == "Expanding" and inflation == "Falling":
        return "Goldilocks"
    if growth == "Expanding" and inflation == "Rising":
        return "Reflation"
    if growth == "Contracting" and inflation == "Rising":
        return "Stagflation"
    return "Deflation"


def with_yoy_inflation(raw: dict[str, pd.Series]) -> dict[str, pd.Series]:
    out = dict(raw)
    for code in YOY_SERIES:
        if code in out:
            out[code] = (out[code].pct_change(12) * 100).dropna()
    return out


def monthly_regimes(raw: dict[str, pd.Series], dates,
                    lags: dict[str, int] = LAG_DAYS) -> pd.DataFrame:
    rows = []
    for d in dates:
        view = as_of_view(raw, pd.Timestamp(d), lags)
        g, gc = compute_growth_direction(view)
        i, ic = compute_inflation_direction(view)
        rows.append({"date": pd.Timestamp(d), "growth": g, "inflation": i,
                     "regime": classify_regime(g, i),
                     "growth_conf": gc, "inflation_conf": ic})
    return pd.DataFrame(rows)


def run_length_summary(labels: pd.Series) -> pd.DataFrame:
    lab = labels.reset_index(drop=True)
    run_id = (lab != lab.shift()).cumsum()
    runs = lab.groupby(run_id).agg(["first", "size"])
    out = runs.groupby("first")["size"].agg(n_runs="count", avg_run_len="mean", n_months="sum")
    out["share"] = out["n_months"] / len(lab)
    return out.reset_index().rename(columns={"first": "regime"})


def transition_matrix(labels: pd.Series) -> pd.DataFrame:
    lab = labels.reset_index(drop=True)
    prev, nxt = lab.shift(), lab
    mask = prev.notna() & (prev != nxt)
    return pd.crosstab(prev[mask], nxt[mask]).rename_axis(index="from", columns="to")
```

- [ ] **Step 4: 통과 확인** → 4 passed
- [ ] **Step 5: Commit** — `✨ feat: 레짐 라벨 소급 계산 (prod/yoy 변형)`

### Task 3: 레짐 조건부 분해 + placebo (`conditional.py`)

**Files:**
- Create: `experiments/market_signals/regime_conditional/conditional.py`
- Test: `experiments/market_signals/tests/test_rc_conditional.py`

**Interfaces:**
- Produces: `join_regime(perdate, regimes) -> pd.DataFrame`,
  `regime_table(joined, ppy=12) -> pd.DataFrame[regime, n, mean, t, sharpe]`,
  `between_stat(returns, labels) -> float`,
  `shift_placebo(returns, labels) -> tuple[obs, p]`,
  `post_change_table(joined) -> pd.DataFrame[phase, n, mean, t]`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""regime_conditional.conditional 테스트."""
import numpy as np
import pandas as pd

from experiments.market_signals.regime_conditional.conditional import (
    between_stat, join_regime, post_change_table, regime_table, shift_placebo,
)


def _regimes(dates, labels):
    return pd.DataFrame({"date": pd.to_datetime(dates), "regime": labels})


def test_join_regime_uses_latest_known_label():
    perdate = pd.DataFrame({"date": pd.to_datetime(["2020-03-31", "2020-04-15"]),
                            "ls": [0.01, 0.02]})
    regimes = _regimes(["2020-02-29", "2020-03-31"], ["A", "B"])
    j = join_regime(perdate, regimes)
    assert list(j["regime"]) == ["B", "B"]


def test_regime_table_stats():
    j = pd.DataFrame({"regime": ["A", "A", "B", "B"], "ls": [0.01, 0.03, -0.01, -0.03]})
    t = regime_table(j).set_index("regime")
    assert t.loc["A", "n"] == 2
    assert abs(t.loc["A", "mean"] - 0.02) < 1e-12
    assert t.loc["A", "sharpe"] > 0 > t.loc["B", "sharpe"]


def test_between_stat_weighted_variance():
    r = np.array([1.0, 1.0, -1.0, -1.0])
    lab = np.array(["A", "A", "B", "B"])
    assert abs(between_stat(r, lab) - 1.0) < 1e-12


def test_shift_placebo_separated_vs_constant():
    lab = np.array(["A"] * 30 + ["B"] * 30)
    strong = np.r_[np.ones(30), -np.ones(30)] + np.linspace(0, 0.01, 60)
    obs, p_strong = shift_placebo(strong, lab)
    assert obs > 0 and p_strong < 0.1
    _, p_flat = shift_placebo(np.zeros(60), lab)
    assert p_flat == 1.0


def test_post_change_table_splits_first_month_after_switch():
    j = pd.DataFrame({"date": pd.date_range("2020-01-31", periods=5, freq="ME"),
                      "regime": ["A", "A", "B", "B", "A"],
                      "ls": [0.01, 0.02, 0.10, 0.03, 0.20]})
    t = post_change_table(j).set_index("phase")
    assert t.loc["post_change", "n"] == 2      # B 첫 달(0.10), A 복귀 첫 달(0.20)
    assert abs(t.loc["post_change", "mean"] - 0.15) < 1e-12
    assert t.loc["steady", "n"] == 3
```

- [ ] **Step 2: 실패 확인** — ModuleNotFoundError
- [ ] **Step 3: 구현**

```python
"""팩터 롱숏 시계열의 레짐 조건부 분해 + 원형 시프트 placebo."""
from __future__ import annotations

import numpy as np
import pandas as pd


def join_regime(perdate: pd.DataFrame, regimes: pd.DataFrame) -> pd.DataFrame:
    p = perdate.sort_values("date").copy()
    r = regimes.sort_values("date")[["date", "regime"]]
    out = pd.merge_asof(p, r, on="date", direction="backward")
    return out.dropna(subset=["regime"]).reset_index(drop=True)


def regime_table(joined: pd.DataFrame, ppy: int = 12) -> pd.DataFrame:
    rows = []
    for reg, grp in joined.groupby("regime"):
        x = grp["ls"].to_numpy()
        n = len(x)
        mean = float(x.mean())
        sd = float(x.std(ddof=1)) if n > 1 else np.nan
        ok = n > 1 and sd > 0
        rows.append({"regime": reg, "n": n, "mean": mean,
                     "t": mean / (sd / np.sqrt(n)) if ok else np.nan,
                     "sharpe": mean / sd * np.sqrt(ppy) if ok else np.nan})
    return pd.DataFrame(rows)


def between_stat(returns: np.ndarray, labels: np.ndarray) -> float:
    grand = returns.mean()
    stat = 0.0
    for lab in np.unique(labels):
        x = returns[labels == lab]
        stat += len(x) * (x.mean() - grand) ** 2
    return float(stat / len(returns))


def shift_placebo(returns: np.ndarray, labels: np.ndarray) -> tuple[float, float]:
    # 라벨의 run 구조(지속성)를 보존하는 모든 원형 시프트를 귀무로 사용
    obs = between_stat(returns, labels)
    n = len(returns)
    hits = sum(between_stat(returns, np.roll(labels, k)) >= obs for k in range(1, n))
    return obs, hits / (n - 1)


def post_change_table(joined: pd.DataFrame) -> pd.DataFrame:
    j = joined.sort_values("date").reset_index(drop=True)
    changed = j["regime"].ne(j["regime"].shift())
    changed.iloc[0] = False
    rows = []
    for name, mask in [("post_change", changed), ("steady", ~changed)]:
        x = j.loc[mask, "ls"].to_numpy()
        n = len(x)
        mean = float(x.mean()) if n else np.nan
        sd = float(x.std(ddof=1)) if n > 1 else np.nan
        rows.append({"phase": name, "n": n, "mean": mean,
                     "t": mean / (sd / np.sqrt(n)) if n > 1 and sd and sd > 0 else np.nan})
    return pd.DataFrame(rows)
```

`post_change_table`의 첫 관측 처리: 첫 달은 "전환 직후"로 세지 않는다(비교 기준이 없음).

- [ ] **Step 4: 통과 확인** → 5 passed
- [ ] **Step 5: Commit** — `✨ feat: 레짐 조건부 분해 + 원형 시프트 placebo`

### Task 4: 오케스트레이션 (`run.py`) + 스모크

**Files:**
- Create: `experiments/market_signals/regime_conditional/run.py`

**Interfaces:**
- Consumes: Task 1–3 전부, `common.config.RESULTS_DIR`,
  `cross_sectional.history.load_long_history`, `vol_targeting.data.equal_weight_returns`.

- [ ] **Step 1: run.py 작성** — 흐름:
  1. FRED 캐시 로드(`results/regime_conditional/fred_cache/`), 월말 그리드 1990-01~2026-06.
  2. `prod`/`yoy` 두 변형의 `monthly_regimes` → CSV + run-length/전이 요약.
  3. 프로덕션 sanity(가능하면): `storage/croesus.duckdb` read-only로 `macro_scores` 최빈 레짐과
     최근 라벨 비교. 실패(락 등) 시 경고 후 스킵.
  4. 7팩터 × 2변형: `perdate_<factor>_21.csv` join → `regime_table` + `shift_placebo`.
  5. 시장(EW) 월별 forward 수익률의 레짐 조건부 표(팩터가 아니라 시장 자체 예측력 확인).
  6. `post_change_table` 팩터별.
  7. CSV 저장: `regimes_{v}.csv`, `regime_summary.csv`, `transitions_{v}.csv`,
     `factor_regime_table.csv`, `placebo.csv`, `market_by_regime.csv`, `post_change.csv`.
- [ ] **Step 2: 스모크** — `RC_FACTORS=momentum_6m python3 -m experiments.market_signals.regime_conditional.run`
- [ ] **Step 3: Commit** — `✨ feat: 레짐 조건부 orchestration (로드맵 ④)`

### Task 5: 전체 실행 + FINDINGS/README

- [ ] 전체 실행(7팩터), 로그 `$CLAUDE_JOB_DIR/tmp/rc_run.log`
- [ ] `FINDINGS.md` — 정직한 결론(라벨 퇴화 발견 포함), `README.md` — 사용법/설계/한계
- [ ] Commit — `📝 docs: 레짐 조건부 FINDINGS + README`

### Task 6: 로드맵 갱신 + push

- [ ] `experiments/RESEARCH_ROADMAP.md` ④ → DONE + 결과 요약 blockquote
- [ ] 전체 테스트 `python3 -m pytest experiments/market_signals/tests/ -q` 통과 확인
- [ ] Commit — `📝 docs: 로드맵 ④ 완료 표기` + push
