# 로드맵 ② 변동성 예측 + 리스크 타게팅 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실현변동성이 naive 대비 예측 가능한지(EWMA/GARCH), 그 예측으로 노출을 조절(vol-targeting)하면 buy-and-hold 대비 MaxDD·Sharpe가 개선되는지 검증한다.

**Architecture:** `experiments/market_signals/vol_targeting/` 자립형 모듈. 순수 계산(실현변동성, 예측기, 평가지표, 오버레이)은 TDD, 데이터 수집·오케스트레이션은 smoke-run. 월별 walk-forward: 각 월말 t에 `returns[:t]`만으로 σ̂ 예측 → [t+1, t+21] 실현변동성과 비교(정확도), 예측 σ̂로 다음 달 노출 결정(오버레이). 대상 자산 2개: SPY(yfinance 1993~, 총수익), 장기 유니버스 등가중 포트폴리오(1990~, ①의 스크래치 DB 재사용).

**Tech Stack:** Python, pandas/numpy/scipy(기존 requirements.txt로 충분 — 신규 의존성 0), DuckDB 스크래치 캐시, pytest.

## Global Constraints

- 프로덕션 DB(`storage/croesus.duckdb`)는 **read-only**로만 접근. 쓰기는 `experiments/market_signals/results/` 아래 스크래치 DB만.
- 루트 `pyproject.toml` 수정 금지. 신규 의존성 없음(scipy MLE로 GARCH 직접 구현).
- 산출물은 `results/vol_targeting/`(이미 results/는 gitignore).
- look-ahead 금지: 시점 t 예측에 t 이후 수익률 사용 금지. 노출은 예측일 **다음 거래일**부터 적용.
- σ_target=0.15(연율), cap∈{1.0, 1.5} 고정 규칙(튜닝 금지 — 과적합 함정).
- 커밋은 gitmoji + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` + `Claude-Session:` 라인.
- 기존 모듈 재사용: `cross_sectional.stats.newey_west_se`, `cross_sectional.portfolio.perf_summary`, `cross_sectional.panel.month_end_grid`, `cross_sectional.history.load_long_history`, `common.config.RESULTS_DIR`.
- `macro_scores.amplifier_score` 비교(로드맵 원문 4단계)는 **불가** — 테이블에 14일치(2026-06-06~)만 존재(역사화 안 됨). FINDINGS에 갭으로 기록하고 스킵.

---

### Task 1: 실현변동성 모듈 (`realized.py`)

**Files:**
- Create: `experiments/market_signals/vol_targeting/__init__.py` (빈 파일)
- Create: `experiments/market_signals/vol_targeting/realized.py`
- Test: `experiments/market_signals/tests/test_vt_realized.py`

**Interfaces:**
- Produces: `TRADING_DAYS: float = 252.0`; `daily_returns(close: pd.Series) -> pd.Series`; `realized_vol(returns: pd.Series, window: int = 21) -> pd.Series`(연율화 trailing); `forward_realized_vol(returns: pd.Series, as_of, horizon: int = 21) -> float`(as_of **이후** horizon일, 연율화, 부족하면 NaN).

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.realized import (
    TRADING_DAYS,
    daily_returns,
    forward_realized_vol,
    realized_vol,
)


def _series(n=100, scale=0.01, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    return pd.Series(100 * np.cumprod(1 + rng.normal(0, scale, n)), index=idx)


def test_daily_returns_matches_pct_change():
    close = _series()
    r = daily_returns(close)
    assert len(r) == len(close) - 1
    assert abs(r.iloc[0] - (close.iloc[1] / close.iloc[0] - 1)) < 1e-12


def test_realized_vol_annualized():
    r = daily_returns(_series(300, scale=0.01))
    rv = realized_vol(r, window=21)
    assert rv.notna().sum() == len(r) - 20
    last = r.iloc[-21:].std(ddof=1) * np.sqrt(TRADING_DAYS)
    assert abs(rv.iloc[-1] - last) < 1e-12


def test_forward_realized_vol_excludes_asof_and_needs_full_window():
    r = daily_returns(_series(60))
    as_of = r.index[30]
    expected = r.iloc[31:52].std(ddof=1) * np.sqrt(TRADING_DAYS)
    assert abs(forward_realized_vol(r, as_of, 21) - expected) < 1e-12
    assert np.isnan(forward_realized_vol(r, r.index[-5], 21))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest experiments/market_signals/tests/test_vt_realized.py -v`
Expected: FAIL (ModuleNotFoundError: vol_targeting.realized)

- [ ] **Step 3: Write minimal implementation**

```python
"""Realized volatility helpers (close-to-close, annualized)."""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252.0


def daily_returns(close: pd.Series) -> pd.Series:
    """Simple daily returns from a close series (index=date)."""
    return close.sort_index().pct_change().dropna()


def realized_vol(returns: pd.Series, window: int = 21) -> pd.Series:
    """Trailing annualized realized vol at each date (full window required)."""
    return returns.rolling(window).std(ddof=1) * np.sqrt(TRADING_DAYS)


def forward_realized_vol(returns: pd.Series, as_of, horizon: int = 21) -> float:
    """Annualized realized vol over the `horizon` trading days AFTER as_of."""
    r = returns.sort_index()
    pos = r.index.searchsorted(pd.Timestamp(as_of), side="right")
    fwd = r.iloc[pos:pos + horizon]
    if len(fwd) < horizon:
        return float("nan")
    return float(fwd.std(ddof=1) * np.sqrt(TRADING_DAYS))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest experiments/market_signals/tests/test_vt_realized.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add experiments/market_signals/vol_targeting/ experiments/market_signals/tests/test_vt_realized.py
git commit -m "✨ feat: realized-vol helpers for vol-targeting experiment (로드맵 ②)"
```

---

### Task 2: naive·EWMA 예측기 (`forecasters.py` 1/2)

**Files:**
- Create: `experiments/market_signals/vol_targeting/forecasters.py`
- Test: `experiments/market_signals/tests/test_vt_forecasters.py`

**Interfaces:**
- Consumes: `realized.TRADING_DAYS`
- Produces: `naive_forecast(returns: pd.Series, window: int = 21) -> float`; `ewma_forecast(returns: pd.Series, lam: float = 0.94) -> float` — 둘 다 연율화 vol, 데이터 부족 시 NaN.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.forecasters import ewma_forecast, naive_forecast
from experiments.market_signals.vol_targeting.realized import TRADING_DAYS


def _returns(n=500, scale=0.01, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2018-01-01", periods=n)
    return pd.Series(rng.normal(0, scale, n), index=idx)


def test_naive_is_trailing_realized_vol():
    r = _returns()
    expected = r.iloc[-21:].std(ddof=1) * np.sqrt(TRADING_DAYS)
    assert abs(naive_forecast(r) - expected) < 1e-12
    assert np.isnan(naive_forecast(r.iloc[:10]))


def test_ewma_near_true_vol_on_iid_series():
    r = _returns(n=2000, scale=0.01)
    f = ewma_forecast(r)
    true_ann = 0.01 * np.sqrt(TRADING_DAYS)
    assert 0.7 * true_ann < f < 1.3 * true_ann
    assert np.isnan(ewma_forecast(r.iloc[:20]))


def test_ewma_reacts_to_recent_vol_jump():
    calm = _returns(n=400, scale=0.005, seed=2)
    idx2 = pd.bdate_range(calm.index[-1] + pd.Timedelta(days=1), periods=60)
    wild = pd.Series(np.random.default_rng(3).normal(0, 0.03, 60), index=idx2)
    f_calm = ewma_forecast(calm)
    f_after = ewma_forecast(pd.concat([calm, wild]))
    assert f_after > 2 * f_calm
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest experiments/market_signals/tests/test_vt_forecasters.py -v`
Expected: FAIL (ModuleNotFoundError: forecasters)

- [ ] **Step 3: Write minimal implementation**

```python
"""Volatility forecasters: naive (trailing RV), EWMA (RiskMetrics), GARCH(1,1)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.realized import TRADING_DAYS


def naive_forecast(returns: pd.Series, window: int = 21) -> float:
    """Forecast = trailing realized vol (annualized). The baseline to beat."""
    r = returns.dropna()
    if len(r) < window:
        return float("nan")
    return float(r.iloc[-window:].std(ddof=1) * np.sqrt(TRADING_DAYS))


def ewma_forecast(returns: pd.Series, lam: float = 0.94) -> float:
    """RiskMetrics EWMA variance recursion, annualized vol forecast."""
    r = returns.dropna().to_numpy(dtype=float)
    if len(r) < 30:
        return float("nan")
    var = float(np.mean(r[:30] ** 2))  # seed with the first month's mean square
    for x in r[30:]:
        var = lam * var + (1 - lam) * x * x
    return float(np.sqrt(var * TRADING_DAYS))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest experiments/market_signals/tests/test_vt_forecasters.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add experiments/market_signals/vol_targeting/forecasters.py experiments/market_signals/tests/test_vt_forecasters.py
git commit -m "✨ feat: naive + EWMA volatility forecasters"
```

---

### Task 3: GARCH(1,1) MLE 예측기 (`forecasters.py` 2/2)

**Files:**
- Modify: `experiments/market_signals/vol_targeting/forecasters.py` (함수 추가)
- Test: `experiments/market_signals/tests/test_vt_forecasters.py` (테스트 추가)

**Interfaces:**
- Produces: `fit_garch11(returns: pd.Series) -> dict` — `{omega, alpha, beta, next_var, converged}`; `garch11_forecast(returns: pd.Series, horizon: int = 21) -> float` — horizon일 평균 조건부 분산의 연율화 vol, 표본 <250이면 NaN. 신규 의존성 없이 scipy Nelder-Mead MLE.

- [ ] **Step 1: Write the failing test** (기존 파일에 추가)

```python
from experiments.market_signals.vol_targeting.forecasters import fit_garch11, garch11_forecast


def _garch_sim(n=3000, omega=2e-6, alpha=0.10, beta=0.85, seed=7):
    rng = np.random.default_rng(seed)
    var = omega / (1 - alpha - beta)
    r = np.empty(n)
    for t in range(n):
        r[t] = np.sqrt(var) * rng.standard_normal()
        var = omega + alpha * r[t] ** 2 + beta * var
    idx = pd.bdate_range("2010-01-01", periods=n)
    return pd.Series(r, index=idx)


def test_garch_recovers_persistence_on_simulated_data():
    fit = fit_garch11(_garch_sim())
    persistence = fit["alpha"] + fit["beta"]
    assert 0.88 < persistence < 0.995
    assert 0.02 < fit["alpha"] < 0.25
    assert fit["next_var"] > 0


def test_garch_forecast_positive_and_sane_on_iid():
    r = _returns(n=1500, scale=0.01, seed=4)
    f = garch11_forecast(r)
    true_ann = 0.01 * np.sqrt(TRADING_DAYS)
    assert 0.6 * true_ann < f < 1.4 * true_ann


def test_garch_needs_history():
    assert np.isnan(garch11_forecast(_returns(n=100)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest experiments/market_signals/tests/test_vt_forecasters.py -v -k garch`
Expected: FAIL (ImportError: fit_garch11)

- [ ] **Step 3: Write minimal implementation** (`forecasters.py`에 추가)

```python
def _garch11_neg_loglik(params: np.ndarray, r: np.ndarray) -> float:
    omega, alpha, beta = params
    if omega <= 0 or alpha < 0 or beta < 0 or alpha + beta >= 0.999:
        return 1e10
    var = np.empty(len(r))
    var[0] = np.var(r)
    for t in range(1, len(r)):
        var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
    var = np.maximum(var, 1e-12)
    return float(0.5 * np.sum(np.log(var) + r ** 2 / var))


def fit_garch11(returns: pd.Series) -> dict:
    """Gaussian MLE of GARCH(1,1) on demeaned daily returns (scipy Nelder-Mead)."""
    from scipy.optimize import minimize

    r = returns.dropna().to_numpy(dtype=float)
    r = r - r.mean()
    v = float(np.var(r))
    x0 = np.array([0.05 * v, 0.08, 0.90])
    res = minimize(_garch11_neg_loglik, x0, args=(r,), method="Nelder-Mead",
                   options={"maxiter": 2000, "xatol": 1e-12, "fatol": 1e-9})
    omega, alpha, beta = (float(p) for p in res.x)
    var = np.empty(len(r))
    var[0] = v
    for t in range(1, len(r)):
        var[t] = omega + alpha * r[t - 1] ** 2 + beta * var[t - 1]
    next_var = max(omega + alpha * r[-1] ** 2 + beta * var[-1], 1e-12)
    return {"omega": omega, "alpha": alpha, "beta": beta,
            "next_var": float(next_var), "converged": bool(res.success)}


def garch11_forecast(returns: pd.Series, horizon: int = 21) -> float:
    """Annualized vol from the mean of the next-`horizon` daily conditional variances."""
    r = returns.dropna()
    if len(r) < 250:
        return float("nan")
    fit = fit_garch11(r)
    omega, a, b = fit["omega"], fit["alpha"], fit["beta"]
    persistence = a + b
    if persistence >= 0.999:
        mean_var = fit["next_var"]
    else:
        uncond = omega / (1 - persistence)
        mean_var = float(np.mean(
            [uncond + persistence ** k * (fit["next_var"] - uncond) for k in range(horizon)]
        ))
    return float(np.sqrt(max(mean_var, 1e-12) * TRADING_DAYS))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest experiments/market_signals/tests/test_vt_forecasters.py -v`
Expected: PASS (6 tests, garch 시뮬 테스트는 수 초 걸릴 수 있음)

- [ ] **Step 5: Commit**

```bash
git add experiments/market_signals/vol_targeting/forecasters.py experiments/market_signals/tests/test_vt_forecasters.py
git commit -m "✨ feat: hand-rolled GARCH(1,1) MLE forecaster (no new deps)"
```

---

### Task 4: 예측 평가 지표 (`evaluate.py`)

**Files:**
- Create: `experiments/market_signals/vol_targeting/evaluate.py`
- Test: `experiments/market_signals/tests/test_vt_evaluate.py`

**Interfaces:**
- Consumes: `cross_sectional.stats.newey_west_se(x, lags)`
- Produces: `mse_loss(forecast_vol, realized_vol) -> np.ndarray`; `qlike_loss(forecast_vol, realized_vol) -> np.ndarray`(분산 기준, 최소값 0 at f=rv); `loss_diff_tstat(loss_a, loss_b, lags=None) -> dict{mean_diff, t, n}`(음수 t ⇒ a가 우수, Diebold-Mariano식 NW t).

- [ ] **Step 1: Write the failing test**

```python
import numpy as np

from experiments.market_signals.vol_targeting.evaluate import (
    loss_diff_tstat,
    mse_loss,
    qlike_loss,
)


def test_mse_zero_at_perfect_forecast():
    rv = np.array([0.1, 0.2, 0.3])
    assert np.allclose(mse_loss(rv, rv), 0.0)
    assert np.allclose(mse_loss(rv + 0.1, rv), 0.01)


def test_qlike_zero_at_perfect_and_positive_otherwise():
    rv = np.array([0.1, 0.2])
    assert np.allclose(qlike_loss(rv, rv), 0.0)
    assert (qlike_loss(rv * 1.5, rv) > 0).all()
    assert (qlike_loss(rv * 0.5, rv) > 0).all()


def test_loss_diff_tstat_sign():
    rng = np.random.default_rng(0)
    rv = np.abs(rng.normal(0.15, 0.03, 200))
    good = mse_loss(rv + rng.normal(0, 0.01, 200), rv)
    bad = mse_loss(rv + rng.normal(0, 0.05, 200), rv)
    res = loss_diff_tstat(good, bad)
    assert res["mean_diff"] < 0 and res["t"] < -2 and res["n"] == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest experiments/market_signals/tests/test_vt_evaluate.py -v`
Expected: FAIL (ModuleNotFoundError: evaluate)

- [ ] **Step 3: Write minimal implementation**

```python
"""Forecast-accuracy metrics: MSE, QLIKE, and a DM-style HAC t-test on loss diffs."""
from __future__ import annotations

import numpy as np

from experiments.market_signals.cross_sectional.stats import newey_west_se


def mse_loss(forecast_vol, realized_vol) -> np.ndarray:
    f = np.asarray(forecast_vol, dtype=float)
    r = np.asarray(realized_vol, dtype=float)
    return (f - r) ** 2


def qlike_loss(forecast_vol, realized_vol) -> np.ndarray:
    """QLIKE on variances: rv/f - log(rv/f) - 1 (0 at f=rv, robust to noisy rv)."""
    f2 = np.asarray(forecast_vol, dtype=float) ** 2
    r2 = np.asarray(realized_vol, dtype=float) ** 2
    ratio = r2 / f2
    return ratio - np.log(ratio) - 1.0


def loss_diff_tstat(loss_a, loss_b, lags: "int | None" = None) -> dict:
    """Diebold-Mariano-style test on mean(loss_a - loss_b); negative t => a better."""
    d = np.asarray(loss_a, dtype=float) - np.asarray(loss_b, dtype=float)
    d = d[np.isfinite(d)]
    mean = float(d.mean()) if len(d) else float("nan")
    se = newey_west_se(d, lags)
    t = mean / se if np.isfinite(se) and se > 0 else float("nan")
    return {"mean_diff": mean, "t": float(t), "n": int(len(d))}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest experiments/market_signals/tests/test_vt_evaluate.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add experiments/market_signals/vol_targeting/evaluate.py experiments/market_signals/tests/test_vt_evaluate.py
git commit -m "✨ feat: QLIKE/MSE + Diebold-Mariano loss-diff test"
```

---

### Task 5: vol-targeting 오버레이 (`overlay.py`)

**Files:**
- Create: `experiments/market_signals/vol_targeting/overlay.py`
- Test: `experiments/market_signals/tests/test_vt_overlay.py`

**Interfaces:**
- Produces: `target_exposure(sigma_hat: float, sigma_target: float = 0.15, cap: float = 1.5) -> float`(NaN/0 예측 → 1.0 fallback); `overlay_returns(daily_ret: pd.Series, exposures: pd.Series, cost_bps: float = 0.0) -> pd.Series` — exposures는 리밸런스일 인덱스, **다음 거래일부터** 적용(look-ahead 금지), 비용 = |Δw|·bps/1e4 (노출 변경일에 부과). 첫 노출 이전 구간은 제외.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.overlay import overlay_returns, target_exposure


def test_target_exposure_rule():
    assert abs(target_exposure(0.30, 0.15, 1.5) - 0.5) < 1e-12
    assert target_exposure(0.05, 0.15, 1.5) == 1.5          # capped
    assert target_exposure(float("nan"), 0.15, 1.5) == 1.0  # fallback


def test_overlay_applies_next_day_no_lookahead():
    idx = pd.bdate_range("2021-01-01", periods=6)
    r = pd.Series([0.01, 0.02, -0.01, 0.03, 0.01, -0.02], index=idx)
    e = pd.Series({idx[1]: 0.5})           # set at day1 close
    out = overlay_returns(r, e)
    assert out.index[0] == idx[2]          # effective from day2
    assert abs(out.loc[idx[2]] - 0.5 * -0.01) < 1e-12


def test_overlay_cost_charged_on_exposure_change():
    idx = pd.bdate_range("2021-01-01", periods=8)
    r = pd.Series(0.0, index=idx)
    e = pd.Series({idx[0]: 1.0, idx[3]: 0.5})
    out = overlay_returns(r, e, cost_bps=10.0)
    # day4: |Δw|=0.5 → cost 0.5 * 10bp = 5bp
    assert abs(out.loc[idx[4]] + 0.5 * 0.0010) < 1e-12
    assert abs(out.loc[idx[2]]) < 1e-12    # no change, no cost


def test_overlay_constant_full_exposure_equals_underlying():
    idx = pd.bdate_range("2021-01-01", periods=5)
    r = pd.Series([0.01, -0.01, 0.02, 0.0, 0.01], index=idx)
    e = pd.Series({idx[0]: 1.0})
    out = overlay_returns(r, e)
    assert np.allclose(out.values, r.iloc[1:].values)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest experiments/market_signals/tests/test_vt_overlay.py -v`
Expected: FAIL (ModuleNotFoundError: overlay)

- [ ] **Step 3: Write minimal implementation**

```python
"""Vol-targeting overlay: exposure rule + step-wise overlay returns with costs."""
from __future__ import annotations

import numpy as np
import pandas as pd


def target_exposure(sigma_hat: float, sigma_target: float = 0.15, cap: float = 1.5) -> float:
    """Exposure = min(cap, target/forecast); unusable forecast falls back to 1.0."""
    if not np.isfinite(sigma_hat) or sigma_hat <= 0:
        return 1.0
    return float(min(cap, sigma_target / sigma_hat))


def overlay_returns(daily_ret: pd.Series, exposures: pd.Series,
                    cost_bps: float = 0.0) -> pd.Series:
    """Apply exposures set at rebalance dates, effective the NEXT trading day.

    Cost = |Δexposure| * cost_bps/1e4, charged on the first day the new
    exposure is live. Days before the first exposure are dropped.
    """
    r = daily_ret.sort_index()
    w = pd.Series(np.nan, index=r.index, dtype=float)
    for dt, val in exposures.sort_index().items():
        pos = r.index.searchsorted(pd.Timestamp(dt), side="right")
        if pos < len(r):
            w.iloc[pos] = float(val)
    w = w.ffill()
    dw = w.diff().abs().fillna(0.0)
    out = r * w - dw * (cost_bps / 1e4)
    return out.dropna()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest experiments/market_signals/tests/test_vt_overlay.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add experiments/market_signals/vol_targeting/overlay.py experiments/market_signals/tests/test_vt_overlay.py
git commit -m "✨ feat: vol-targeting exposure rule + overlay backtest returns"
```

---

### Task 6: 데이터 로딩 (`data.py`)

**Files:**
- Create: `experiments/market_signals/vol_targeting/data.py`
- Test: `experiments/market_signals/tests/test_vt_data.py` (순수 함수 `equal_weight_returns`만; 네트워크 fetch는 smoke)

**Interfaces:**
- Consumes: `common.config.RESULTS_DIR`, `cross_sectional.history.load_long_history(min_rows, start_year)`
- Produces: `fetch_spy(start="1993-01-29") -> None`(yfinance → 스크래치 DB `results/vol_targeting/index_history.duckdb`, 캐시 있으면 no-op); `load_spy() -> pd.Series`(Adj Close, index=date); `equal_weight_returns(prices: dict[str, pd.DataFrame], min_names: int = 30) -> pd.Series`; `load_ew_returns(start_year: int = 1990) -> pd.Series`.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pandas as pd

from experiments.market_signals.vol_targeting.data import equal_weight_returns


def _frame(closes, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame({"close": closes}, index=idx)


def test_equal_weight_is_mean_of_daily_returns():
    prices = {"a": _frame([100, 110, 121]), "b": _frame([100, 90, 99])}
    ew = equal_weight_returns(prices, min_names=2)
    assert len(ew) == 2
    assert abs(ew.iloc[0] - np.mean([0.10, -0.10])) < 1e-12
    assert abs(ew.iloc[1] - np.mean([0.10, 0.10])) < 1e-12


def test_min_names_filters_thin_days():
    prices = {"a": _frame([100, 110, 121]), "b": _frame([100, 90])}
    ew = equal_weight_returns(prices, min_names=2)
    assert len(ew) == 1  # day3 has only 'a'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest experiments/market_signals/tests/test_vt_data.py -v`
Expected: FAIL (ModuleNotFoundError: data)

- [ ] **Step 3: Write minimal implementation**

```python
"""Price series for the vol-targeting experiment (로드맵 ②).

Assets:
  * SPY — fetched once from yfinance (1993~, Adj Close = total return) into a
    scratch DuckDB under results/. Production DB is never touched.
  * EW  — equal-weight daily portfolio of the cross-sectional long-history
    universe (523 survivors, 1990~). Survivorship inflates its return LEVEL,
    but the vol-targeting comparison is internal (same portfolio, scaled
    exposure), so the overlay-vs-B&H comparison stays fair.
"""
from __future__ import annotations

import duckdb
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.history import load_long_history

SCRATCH_DB = RESULTS_DIR / "vol_targeting" / "index_history.duckdb"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS index_prices (
    symbol TEXT, date DATE, adj_close DOUBLE,
    PRIMARY KEY (symbol, date)
)
"""


def _connect() -> duckdb.DuckDBPyConnection:
    SCRATCH_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(SCRATCH_DB))
    con.execute(_SCHEMA)
    return con


def fetch_spy(start: str = "1993-01-29") -> None:
    """One-time SPY download into the scratch cache (no-op when cached)."""
    import yfinance as yf

    con = _connect()
    try:
        n = con.execute("SELECT COUNT(*) FROM index_prices WHERE symbol='SPY'").fetchone()[0]
        if n > 1000:
            return
        raw = yf.download("SPY", start=start, end="2026-06-30",
                          auto_adjust=False, progress=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.dropna(subset=["Adj Close"])
        rows = [("SPY", pd.Timestamp(dt).date(), float(v))
                for dt, v in raw["Adj Close"].items() if float(v) > 0]
        con.executemany("INSERT OR REPLACE INTO index_prices VALUES (?,?,?)", rows)
        print(f"[data] cached {len(rows)} SPY rows", flush=True)
    finally:
        con.close()


def load_spy() -> pd.Series:
    con = _connect()
    try:
        df = con.execute(
            "SELECT date, adj_close FROM index_prices WHERE symbol='SPY' ORDER BY date"
        ).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["adj_close"].rename("close")


def equal_weight_returns(prices: dict[str, pd.DataFrame], min_names: int = 30) -> pd.Series:
    """Daily equal-weight mean return across all names with data that day."""
    wide = pd.DataFrame({aid: df["close"].pct_change() for aid, df in prices.items()})
    counts = wide.notna().sum(axis=1)
    return wide.mean(axis=1)[counts >= min_names].dropna().rename("ew_ret")


def load_ew_returns(start_year: int = 1990) -> pd.Series:
    return equal_weight_returns(load_long_history(start_year=start_year))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest experiments/market_signals/tests/test_vt_data.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Smoke — SPY fetch + 로드 확인**

Run: `python -c "from experiments.market_signals.vol_targeting.data import fetch_spy, load_spy; fetch_spy(); s = load_spy(); print(s.index.min(), s.index.max(), len(s))"`
Expected: `1993-01-29 ... 2026-06-2x ... ~8400` (스크래치 DB 생성, 프로덕션 무접촉)

- [ ] **Step 6: Commit**

```bash
git add experiments/market_signals/vol_targeting/data.py experiments/market_signals/tests/test_vt_data.py
git commit -m "✨ feat: SPY long-history cache + equal-weight universe returns"
```

---

### Task 7: 오케스트레이션 (`run.py`) + 스모크

**Files:**
- Create: `experiments/market_signals/vol_targeting/run.py`

**Interfaces:**
- Consumes: 위 전 모듈 + `cross_sectional.panel.month_end_grid(all_dates, start_year)` + `cross_sectional.portfolio.perf_summary(returns, periods_per_year)`
- Produces: `results/vol_targeting/` 아래 `accuracy_<asset>.csv`(예측기×{mse, qlike, dm_t vs naive}), `overlay_<asset>.csv`(전략×cap×cost → sharpe/maxdd/cum/turnover), `perdate_<asset>.csv`(월별 예측·실현·노출), `curve_<asset>_cap{cap}_c{bps}.csv`(일별 전략 수익률 wide).

- [ ] **Step 1: Write the orchestration**

```python
"""로드맵 ② orchestration — walk-forward vol forecasts + vol-targeting overlay.

Run from repo root (venv with experiments/market_signals/requirements.txt):
  python -m experiments.market_signals.vol_targeting.run
Env:
  VT_ASSETS=spy,ew    subset of assets (default both)
  VT_START_YEAR=1995  first rebalance year (warmup uses earlier data)

Walk-forward: at each month-end t, forecast annualized vol for the next 21
trading days using returns[:t] only; compare with forward realized vol
(accuracy) and set next month's exposure = min(cap, 0.15/sigma_hat) (overlay).
GARCH refits monthly on the trailing 2000 obs. 'oracle' uses the forward
realized vol itself — a look-ahead upper bound, reported for diagnostics only.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from experiments.market_signals.common.config import RESULTS_DIR
from experiments.market_signals.cross_sectional.panel import month_end_grid
from experiments.market_signals.cross_sectional.portfolio import perf_summary
from experiments.market_signals.vol_targeting.data import fetch_spy, load_ew_returns, load_spy
from experiments.market_signals.vol_targeting.evaluate import loss_diff_tstat, mse_loss, qlike_loss
from experiments.market_signals.vol_targeting.forecasters import (
    ewma_forecast,
    garch11_forecast,
    naive_forecast,
)
from experiments.market_signals.vol_targeting.overlay import overlay_returns, target_exposure
from experiments.market_signals.vol_targeting.realized import daily_returns, forward_realized_vol

OUT = RESULTS_DIR / "vol_targeting"
HORIZON = 21
SIGMA_TARGET = 0.15
CAPS = [1.0, 1.5]
COSTS_BPS = [0.0, 10.0]
GARCH_WINDOW = 2000
MIN_WARMUP = 750
FORECASTERS = ["naive", "ewma", "garch"]
START_YEAR = int(os.environ.get("VT_START_YEAR", "1995"))


def _load_assets() -> dict[str, pd.Series]:
    which = os.environ.get("VT_ASSETS", "spy,ew").split(",")
    out: dict[str, pd.Series] = {}
    if "spy" in which:
        fetch_spy()
        out["spy"] = daily_returns(load_spy())
    if "ew" in which:
        out["ew"] = load_ew_returns()
    return out


def _forecast_table(returns: pd.Series) -> pd.DataFrame:
    """Monthly walk-forward forecasts + forward realized vol, one row per month-end."""
    grid = [d for d in month_end_grid(list(returns.index), START_YEAR)
            if returns.index.searchsorted(d, side="right") >= MIN_WARMUP]
    rows = []
    for i, dt in enumerate(grid):
        hist = returns.loc[:dt]
        row = {
            "date": dt,
            "naive": naive_forecast(hist),
            "ewma": ewma_forecast(hist),
            "garch": garch11_forecast(hist.iloc[-GARCH_WINDOW:], HORIZON),
            "realized": forward_realized_vol(returns, dt, HORIZON),
        }
        rows.append(row)
        if (i + 1) % 60 == 0:
            print(f"  ... {i + 1}/{len(grid)} months", flush=True)
    return pd.DataFrame(rows).set_index("date")


def _accuracy(tbl: pd.DataFrame) -> pd.DataFrame:
    ok = tbl.dropna(subset=["realized"])
    rows = []
    base_mse = mse_loss(ok["naive"], ok["realized"])
    base_ql = qlike_loss(ok["naive"], ok["realized"])
    for f in FORECASTERS:
        sub = ok.dropna(subset=[f])
        mse = mse_loss(sub[f], sub["realized"])
        ql = qlike_loss(sub[f], sub["realized"])
        dm_mse = loss_diff_tstat(mse_loss(ok[f], ok["realized"]), base_mse)
        dm_ql = loss_diff_tstat(qlike_loss(ok[f], ok["realized"]), base_ql)
        rows.append({"forecaster": f, "n": len(sub),
                     "mse": float(np.nanmean(mse)), "qlike": float(np.nanmean(ql)),
                     "dm_t_mse_vs_naive": dm_mse["t"], "dm_t_qlike_vs_naive": dm_ql["t"]})
    return pd.DataFrame(rows)


def _overlays(returns: pd.Series, tbl: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    years = (returns.index[-1] - returns.index[0]).days / 365.25
    ppy = len(returns) / years
    strategies = {"bnh": None, "oracle": "realized",
                  **{f: f for f in FORECASTERS}}
    rows, curves = [], {}
    for cap in CAPS:
        for cost in COSTS_BPS:
            daily = {}
            for name, col in strategies.items():
                if name == "bnh":
                    expo = pd.Series(1.0, index=tbl.index)
                else:
                    expo = tbl[col].map(
                        lambda s: target_exposure(s, SIGMA_TARGET, cap))
                ret = overlay_returns(returns, expo, cost)
                daily[name] = ret
                p = perf_summary(ret, ppy)
                to = float(expo.diff().abs().mean()) if name != "bnh" else 0.0
                rows.append({"strategy": name, "cap": cap, "cost_bps": cost,
                             "sharpe": p["sharpe"], "maxdd": p["maxdd"], "cum": p["cum"],
                             "ann_vol": p["vol"] * np.sqrt(ppy),
                             "avg_monthly_turnover": to,
                             "avg_exposure": float(expo.mean())})
            curves[(cap, cost)] = pd.DataFrame(daily)
    return pd.DataFrame(rows), curves


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for asset, returns in _load_assets().items():
        print(f"[vt] {asset}: {len(returns)} days "
              f"{returns.index[0].date()}..{returns.index[-1].date()}", flush=True)
        tbl = _forecast_table(returns)
        tbl.to_csv(OUT / f"perdate_{asset}.csv")
        acc = _accuracy(tbl)
        acc.to_csv(OUT / f"accuracy_{asset}.csv", index=False)
        print(f"[vt] {asset} accuracy:\n{acc.round(4).to_string(index=False)}", flush=True)
        ov, curves = _overlays(returns, tbl)
        ov.to_csv(OUT / f"overlay_{asset}.csv", index=False)
        for (cap, cost), df in curves.items():
            df.to_csv(OUT / f"curve_{asset}_cap{cap:g}_c{cost:g}.csv")
        print(f"[vt] {asset} overlay:\n{ov.round(3).to_string(index=False)}", flush=True)
    print(f"[vt] wrote results to {OUT}", flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 전체 테스트 확인**

Run: `python -m pytest experiments/market_signals/tests/ -q`
Expected: 기존 40개 + 신규 ~15개 전부 PASS

- [ ] **Step 3: 스모크 실행 (SPY만, 최근 시작연도로 빠르게)**

Run: `VT_ASSETS=spy VT_START_YEAR=2018 python -m experiments.market_signals.vol_targeting.run`
Expected: accuracy/overlay 표가 stdout에 출력, `results/vol_targeting/*.csv` 생성, NaN 도배 없음

- [ ] **Step 4: Commit**

```bash
git add experiments/market_signals/vol_targeting/run.py
git commit -m "✨ feat: vol-targeting walk-forward orchestration (로드맵 ②)"
```

---

### Task 8: 전체 실행 + FINDINGS + 문서

**Files:**
- Create: `experiments/market_signals/vol_targeting/FINDINGS.md`
- Create: `experiments/market_signals/vol_targeting/README.md`
- Modify: `experiments/RESEARCH_ROADMAP.md` (② 상태 DONE + 결과 요약 blockquote)

- [ ] **Step 1: 풀 실행 (SPY 1995~ + EW 1995~, 백그라운드)**

Run: `python -m experiments.market_signals.vol_targeting.run` (수 분~수십 분: GARCH 월별 재적합 ~800회)
Expected: `results/vol_targeting/`에 자산별 accuracy/overlay/perdate/curve CSV

- [ ] **Step 2: 결과 해석 체크리스트로 FINDINGS.md 작성**

필수 포함(정직한 결론):
- 가설 (a) 예측 정확도: EWMA/GARCH가 naive를 QLIKE/MSE에서 이기는가? DM t-stat 병기. (문헌상 QLIKE에서 이기는 게 정상.)
- 가설 (b) 오버레이: cap 1.0/1.5 × cost 0/10bps에서 Sharpe·MaxDD 비교. **MaxDD 감소가 핵심 지표**(로드맵 성공 기준). oracle과의 격차 = 예측 개선 여지.
- 함정 명시: cash 수익률 0% 가정(보수적), σ_target 고정(튜닝 안 함), EW 자산의 survivorship(수익 수준은 부풀지만 오버레이 비교는 내부적으로 공정), amplifier_score 비교 불가(macro_scores 14일치 — 역사화 갭), 단일 자산 2개(다중검정 아님).
- ①과의 연결: low-vol 아노말리(종목 간)와 vol-targeting(시계열)은 다른 주장 — 여기서는 시계열 vol 예측성만 검증.

- [ ] **Step 3: README.md 작성** (실행법, 방법 요약, 산출물 목록, 한계 — cross_sectional/README.md 포맷 따름)

- [ ] **Step 4: 로드맵 업데이트** — ② 행 상태를 **DONE**으로, ① 요약 blockquote 아래에 ② 요약 blockquote 추가(결과 수치 포함)

- [ ] **Step 5: Commit + push**

```bash
git add experiments/market_signals/vol_targeting/ experiments/RESEARCH_ROADMAP.md
git commit -m "📝 docs: 로드맵 ② findings — vol forecastability + risk targeting"
git push
```
