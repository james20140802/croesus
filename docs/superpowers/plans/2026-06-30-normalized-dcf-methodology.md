# Normalized DCF Opportunity Methodology — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a new, separate `normalized_dcf` opportunity methodology built on a *reverse DCF + normalized FCF* design, while extending FCF ingest to ~10 years and leaving the existing mechanical DCF (Methodology A) completely untouched.

**Architecture:** A new methodology computes, per asset, (1) a **normalized base FCF** (median of recent FCF, not the latest trough/peak year), (2) a **reference growth** (log-linear regression slope of ln(FCF), robust to endpoint artifacts), (3) a **normalized forward intrinsic value**, and (4) a **reverse-DCF implied growth** — the FCF growth the market price is pricing in. The headline ranking signal is `plausibility_gap = implied_growth − reference_growth` (small/negative = cheap, large = priced for a lot). Results land in a new `normalized_dcf_snapshots` table and feed a new `_review_methodology_normalized_dcf` selectable from the existing opportunity registry. Pure math is DB-free in `croesus/factors/equity/normalized.py`; orchestration reads the DB and reuses the WACC already persisted by the mechanical run.

**Tech Stack:** Python 3, DuckDB, existing `croesus.factors.equity.valuation` pure-math layer, the `OPPORTUNITY_METHODOLOGIES` registry, the `local_sync` / `quarterly_run` pipeline. Tests with pytest (flat `tests/`).

## Design rationale (worked example — AAPL, observed 2026-06-30)

- AAPL annual FCF (DB): 2022 $111.4B (COVID peak) → 2023 $99.6B → 2024 $108.8B → 2025 $98.8B (trough). The current model's endpoint CAGR `(98.8/111.4)^(1/3)−1 = −3.9%` is an **endpoint artifact**; the series is ~flat. Median = $104.2B; log-linear slope = −2.7%.
- Reverse DCF on the moat-adjusted knobs (10y CAP, term 3.0%, WACC 11.49%): the market price ($281.74) implies **+20.0%/yr** FCF growth for 10 years. Mechanical knobs (5y/2.5%) imply +32.4%/yr.
- `plausibility_gap = 20.0% − (−2.7%) = 22.7 points` → "priced for a lot." This converts the unfalsifiable "is the model too conservative?" into a checkable "is 20% FCF growth believable?".
- Normalizing base+growth raises AAPL's floor from 46.24 → 53.64 (loglin) or 65.27 (flat), so the normalized intrinsic is honestly higher without touching Methodology A.

Cross-section findings that shaped the design (observed across AAPL/MSFT/GOOGL/JPM/XOM):
- `median_YoY` growth is too noisy with few points (AAPL −9.2%) → **rejected**. `loglinear_slope` converges with peers on clean names (GOOGL ~6.7%) → **adopted**.
- **Financials (JPM)**: FCF is meaningless (107 → 13 → −42 → −148B). Reverse DCF undefined → must emit `valuation_quality = "fcf_not_meaningful"` and skip, never crash.
- **4 years is too few** for stable normalization (MSFT estimators ranged 3–8%) → extend ingest to ~10y; flag `"short_history"` when fewer years are available.

## Global Constraints

- **Do not modify** the existing mechanical DCF path: `two_stage_dcf`, `compute_fcf_growth`, `value_with_knobs`, `compute_and_store_valuation_factors`, `valuation_snapshots`, `intrinsic_value_bands`, or Methodology A (`_review_methodology_a`). The new methodology is additive and coexists for forward-testing.
- **Deterministic only.** No LLM-generated numbers. `reference_growth` is pure FCF math; thesis grades are explicitly out of scope for this plan (deferred).
- Pure valuation math stays **DB-free** in `croesus/factors/equity/` math modules; DB reads/writes live in orchestration/repository modules (mirror `valuation.py` vs `compute_valuation.py`).
- **Per-asset failures must never stop the run** — wrap each asset in try/except and record a skip reason.
- Keep `metric_name` / `factor_name` strings stable. New table column names, once chosen here, are stable primary-key-adjacent identifiers.
- Schema changes go into `croesus/db/schema.sql` as idempotent `CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`; `migrate()` runs the whole file every time.
- Tests are flat in `tests/`. Integration tests use `migrate(tmp_path/"croesus.duckdb")` + `seed_us_equities(conn)` (seeds AAPL=`US_EQ_AAPL`, MSFT=`US_EQ_MSFT`, NVDA=`US_EQ_NVDA`).
- Commits use gitmoji per `CLAUDE.md` (✨ feat / 🧪 tests / 🗃️ schema / 📝 docs). Commit frequently and atomically.

---

## File Structure

- **Create** `croesus/factors/equity/normalized.py` — pure, DB-free math: `normalized_base_fcf`, `loglinear_fcf_growth`, `reverse_dcf_implied_growth`, `evaluate_normalized_dcf`, `NormalizedDcfResult`, quality constants.
- **Create** `croesus/factors/equity/normalized_repository.py` — `NormalizedDcfSnapshot` dataclass + `NormalizedDcfRepository` (upsert / get / load_latest).
- **Create** `croesus/factors/equity/compute_normalized_dcf.py` — orchestration: read prices/fundamentals + reuse mechanical WACC, call pure math, persist.
- **Modify** `croesus/data_sources/fundamentals/yfinance_fundamentals.py` — fetch ~10y annual cashflow.
- **Modify** `croesus/db/schema.sql` — append `normalized_dcf_snapshots` table.
- **Modify** `croesus/opportunities/selection.py` — register `normalized_dcf` methodology.
- **Modify** `croesus/opportunities/review.py` — add optional `OpportunityCard` fields, `_review_methodology_normalized_dcf`, dispatch.
- **Modify** `croesus/jobs/quarterly_run.py` and `croesus/web/scheduler.py` — run the compute after mechanical valuation.
- **Modify** `croesus/reports/opportunity.py` — render normalized-methodology fields.
- **Tests**: `tests/test_normalized_dcf_math.py`, `tests/test_normalized_dcf_repository.py`, `tests/test_normalized_dcf_compute.py`, and additions to `tests/test_opportunity_review.py`, `tests/test_fundamentals.py`.

---

### Task 1: Extend FCF ingest to ~10 annual years

**Files:**
- Modify: `croesus/data_sources/fundamentals/yfinance_fundamentals.py` (the `cashflow_annual` fetch)
- Test: `tests/test_fundamentals.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: more annual rows in `fundamentals` for `metric_name='free_cash_flow'` (and other cashflow metrics) when the provider returns them. No signature change to `ingest_fundamentals` or the repository.

**Risk note (record in commit body):** yfinance free-tier annual cashflow often returns only ~4 years; `get_cashflow(freq="yearly")` returns up to ~10 *when available*. This task is **best-effort** — downstream code must degrade via the `short_history` flag (Task 4), never assume 10 years exist.

- [ ] **Step 1: Write the failing test** (provider returns 10 annual columns → all are extracted)

In `tests/test_fundamentals.py`, add:

```python
def test_ingest_stores_all_available_fcf_years(tmp_path: Path) -> None:
    # A fake cashflow frame with 10 annual periods must yield 10 stored FCF rows.
    import pandas as pd
    from croesus.fundamentals.ingest_fundamentals import _extract
    cols = [pd.Timestamp(f"{y}-09-30") for y in range(2016, 2026)]
    frame = pd.DataFrame({c: [100.0 + i] for i, c in enumerate(cols)},
                         index=["Free Cash Flow"])
    metrics = _extract("US_EQ_AAPL", frame, _CASHFLOW_LABELS, period_type="annual",
                       source="yfinance")
    fcf = [m for m in metrics if m.metric_name == "free_cash_flow"]
    assert len(fcf) == 10
```

Adjust the import of `_CASHFLOW_LABELS` / `_extract` to match their actual location in `croesus/fundamentals/ingest_fundamentals.py` (confirm names with `grep -n "_CASHFLOW_LABELS\|def _extract" croesus/fundamentals/ingest_fundamentals.py`).

- [ ] **Step 2: Run test to verify it fails (or passes trivially)**

Run: `pytest tests/test_fundamentals.py::test_ingest_stores_all_available_fcf_years -v`
Expected: PASS for the extractor (it already iterates all columns) — this test pins the invariant that *all* provider columns are stored. If it fails, fix the import path, not the extractor.

- [ ] **Step 3: Switch the provider to request yearly history**

In `croesus/data_sources/fundamentals/yfinance_fundamentals.py`, change the cashflow fetch from the `ticker.cashflow` attribute to the method that returns more years. Replace the `cashflow_annual=self._frame(ticker, "cashflow")` line with:

```python
cashflow_annual=self._get_yearly(ticker, "cashflow"),
```

and add a helper next to `_frame`:

```python
def _get_yearly(self, ticker, kind: str):
    """Annual statement with the widest history yfinance offers.

    `ticker.cashflow` returns ~4 columns; `get_cashflow(freq="yearly")`
    returns up to ~10 when available. Falls back to the attribute if the
    method is absent or raises (older yfinance / offline).
    """
    getter = getattr(ticker, f"get_{kind}", None)
    if getter is not None:
        try:
            frame = getter(freq="yearly")
            if frame is not None and not frame.empty:
                return frame
        except Exception:  # noqa: BLE001 - fall back to the attribute below
            pass
    return self._frame(ticker, kind)
```

(Confirm `_frame`'s exact signature first; match its return type. Only the cashflow call needs widening for this plan — income/balance can stay as-is unless `_frame` is trivially shared.)

- [ ] **Step 4: Run the fundamentals test suite**

Run: `pytest tests/test_fundamentals.py -v`
Expected: PASS (no regressions; the new extractor test green).

- [ ] **Step 5: Commit**

```bash
git add croesus/data_sources/fundamentals/yfinance_fundamentals.py tests/test_fundamentals.py
git commit -m "✨ feat: fetch up to ~10 years of annual FCF (best-effort) for DCF normalization"
```

---

### Task 2: Pure math — normalized base FCF + log-linear growth

**Files:**
- Create: `croesus/factors/equity/normalized.py`
- Test: `tests/test_normalized_dcf_math.py`

**Interfaces:**
- Consumes: `FCF_GROWTH_FLOOR`, `FCF_GROWTH_CAP` from `croesus.factors.equity.valuation`.
- Produces:
  - `NORMALIZED_FCF_WINDOW = 10`, `MIN_NORMALIZED_FCF_YEARS = 4`, `MIN_POSITIVE_FCF_POINTS = 2`
  - `normalized_base_fcf(annual_fcf: list[float], *, window: int = NORMALIZED_FCF_WINDOW) -> float | None`
  - `loglinear_fcf_growth(annual_fcf: list[float], *, window: int = NORMALIZED_FCF_WINDOW) -> float | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_normalized_dcf_math.py`:

```python
from __future__ import annotations

import math

import pytest

from croesus.factors.equity.normalized import (
    loglinear_fcf_growth,
    normalized_base_fcf,
)


def test_normalized_base_fcf_is_median_not_last():
    # last year is a trough; median ignores it.
    assert normalized_base_fcf([111.4, 99.6, 108.8, 98.8]) == pytest.approx(104.2)


def test_normalized_base_fcf_empty_is_none():
    assert normalized_base_fcf([]) is None


def test_loglinear_growth_recovers_constant_rate():
    series = [100.0 * 1.10**i for i in range(6)]  # exact 10%/yr
    assert loglinear_fcf_growth(series) == pytest.approx(0.10, abs=1e-9)


def test_loglinear_growth_ignores_endpoint_spike():
    # flat ~100 with one peak first year -> log-linear stays near 0, unlike endpoint CAGR.
    series = [130.0, 100.0, 101.0, 99.0, 100.0]
    g = loglinear_fcf_growth(series)
    assert -0.10 < g < 0.02


def test_loglinear_growth_none_when_too_few_positive_points():
    assert loglinear_fcf_growth([-5.0, -3.0, 10.0]) is None  # only 1 positive point


def test_loglinear_growth_is_clipped():
    series = [1.0 * 3.0**i for i in range(5)]  # 200%/yr, must clip to cap
    from croesus.factors.equity.valuation import FCF_GROWTH_CAP
    assert loglinear_fcf_growth(series) == pytest.approx(FCF_GROWTH_CAP)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_normalized_dcf_math.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.factors.equity.normalized'`.

- [ ] **Step 3: Implement the two functions**

Create `croesus/factors/equity/normalized.py`:

```python
"""Normalized-FCF DCF math (reverse-DCF methodology).

Pure and DB-free, mirroring :mod:`croesus.factors.equity.valuation`. Normalizes
the FCF *level* (median, not the latest trough/peak year) and the FCF *growth*
(log-linear regression slope, robust to endpoint artifacts), then powers a
reverse DCF that solves for the growth the market price implies.
"""
from __future__ import annotations

import math
import statistics

from croesus.factors.equity.valuation import FCF_GROWTH_CAP, FCF_GROWTH_FLOOR

NORMALIZED_FCF_WINDOW = 10        # years of FCF history to normalize over
MIN_NORMALIZED_FCF_YEARS = 4      # fewer available -> "short_history" flag
MIN_POSITIVE_FCF_POINTS = 2       # fewer positive points -> growth undefined


def normalized_base_fcf(
    annual_fcf: list[float], *, window: int = NORMALIZED_FCF_WINDOW
) -> float | None:
    """Median of the most recent ``window`` annual FCF values (``None`` if empty).

    Median (vs the latest year) damps a single peak/trough year — the endpoint
    artifact that makes a flat compounder look like a decliner.
    """
    recent = annual_fcf[-window:]
    if not recent:
        return None
    return statistics.median(recent)


def loglinear_fcf_growth(
    annual_fcf: list[float], *, window: int = NORMALIZED_FCF_WINDOW
) -> float | None:
    """Annualized growth = ``exp(OLS slope of ln(FCF) on year index) - 1``.

    Uses only positive points within the most recent ``window`` years, keeping
    their original index spacing so gaps from skipped (non-positive) years are
    preserved. ``None`` when fewer than ``MIN_POSITIVE_FCF_POINTS`` positive
    points exist (growth across a sign change is undefined). Clipped to the same
    ``[FCF_GROWTH_FLOOR, FCF_GROWTH_CAP]`` band as the mechanical model.
    """
    recent = annual_fcf[-window:]
    points = [(i, v) for i, v in enumerate(recent) if v > 0]
    if len(points) < MIN_POSITIVE_FCF_POINTS:
        return None
    xs = [i for i, _ in points]
    ys = [math.log(v) for _, v in points]
    n = len(xs)
    xbar = sum(xs) / n
    ybar = sum(ys) / n
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    sxx = sum((x - xbar) ** 2 for x in xs)
    if sxx == 0:
        return None
    growth = math.exp(sxy / sxx) - 1.0
    return max(FCF_GROWTH_FLOOR, min(FCF_GROWTH_CAP, growth))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_normalized_dcf_math.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add croesus/factors/equity/normalized.py tests/test_normalized_dcf_math.py
git commit -m "✨ feat: normalized base FCF (median) and log-linear FCF growth"
```

---

### Task 3: Pure math — reverse DCF implied growth

**Files:**
- Modify: `croesus/factors/equity/normalized.py`
- Test: `tests/test_normalized_dcf_math.py`

**Interfaces:**
- Consumes: `two_stage_dcf`, `DcfKnobs`, `DEFAULT_DCF_KNOBS` from `croesus.factors.equity.valuation`.
- Produces: `reverse_dcf_implied_growth(*, price: float, base_fcf: float, wacc: float, shares_outstanding: float, total_debt: float | None, cash: float | None, knobs: DcfKnobs = DEFAULT_DCF_KNOBS, lo: float = -0.50, hi: float = 1.00, iterations: int = 100) -> float | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_normalized_dcf_math.py`:

```python
def test_reverse_dcf_recovers_known_growth():
    from croesus.factors.equity.normalized import reverse_dcf_implied_growth
    from croesus.factors.equity.valuation import DEFAULT_DCF_KNOBS, two_stage_dcf
    kw = dict(base_fcf=100.0, wacc=0.10, shares_outstanding=10.0,
              total_debt=0.0, cash=0.0, knobs=DEFAULT_DCF_KNOBS)
    forward = two_stage_dcf(growth_rate=0.10, **kw)
    price = forward.intrinsic_value_per_share
    implied = reverse_dcf_implied_growth(price=price, **kw)
    assert implied == pytest.approx(0.10, abs=1e-4)


def test_reverse_dcf_none_when_price_above_search_range():
    from croesus.factors.equity.normalized import reverse_dcf_implied_growth
    # Absurdly high price -> implied growth > hi cap -> None (out of range).
    implied = reverse_dcf_implied_growth(
        price=1e12, base_fcf=100.0, wacc=0.10, shares_outstanding=10.0,
        total_debt=0.0, cash=0.0)
    assert implied is None


def test_reverse_dcf_none_on_invalid_inputs():
    from croesus.factors.equity.normalized import reverse_dcf_implied_growth
    assert reverse_dcf_implied_growth(
        price=50.0, base_fcf=-1.0, wacc=0.10, shares_outstanding=10.0,
        total_debt=0.0, cash=0.0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_normalized_dcf_math.py -k reverse -v`
Expected: FAIL with `ImportError: cannot import name 'reverse_dcf_implied_growth'`.

- [ ] **Step 3: Implement the reverse solver**

Append to `croesus/factors/equity/normalized.py` (add imports at top: `from croesus.factors.equity.valuation import DEFAULT_DCF_KNOBS, DcfKnobs, two_stage_dcf`):

```python
def reverse_dcf_implied_growth(
    *,
    price: float,
    base_fcf: float,
    wacc: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    knobs: DcfKnobs = DEFAULT_DCF_KNOBS,
    lo: float = -0.50,
    hi: float = 1.00,
    iterations: int = 100,
) -> float | None:
    """FCF growth ``g`` such that the two-stage DCF intrinsic equals ``price``.

    Intrinsic is monotonically increasing in ``g``, so we bracket-check then
    bisect on ``[lo, hi]``. ``None`` when inputs are invalid (``base_fcf <= 0``,
    no shares, ``wacc <= terminal``) or the price is not bracketed within the
    search range (i.e. implied growth is outside ``[lo, hi]`` — e.g. a name
    priced for >100% growth).
    """
    if base_fcf <= 0 or shares_outstanding <= 0 or wacc <= knobs.terminal_growth_rate:
        return None

    def intrinsic(g: float) -> float | None:
        result = two_stage_dcf(
            base_fcf=base_fcf, growth_rate=g, wacc=wacc,
            shares_outstanding=shares_outstanding,
            total_debt=total_debt, cash=cash, knobs=knobs,
        )
        return result.intrinsic_value_per_share if result else None

    low_v, high_v = intrinsic(lo), intrinsic(hi)
    if low_v is None or high_v is None or not (low_v <= price <= high_v):
        return None

    for _ in range(iterations):
        mid = (lo + hi) / 2
        v = intrinsic(mid)
        if v is None or v < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_normalized_dcf_math.py -v`
Expected: PASS (all math tests, 9 total).

- [ ] **Step 5: Commit**

```bash
git add croesus/factors/equity/normalized.py tests/test_normalized_dcf_math.py
git commit -m "✨ feat: reverse DCF solver for market-implied FCF growth"
```

---

### Task 4: Pure assembler — `evaluate_normalized_dcf` + quality flags

**Files:**
- Modify: `croesus/factors/equity/normalized.py`
- Test: `tests/test_normalized_dcf_math.py`

**Interfaces:**
- Produces:
  - Quality constants `QUALITY_OK = "ok"`, `QUALITY_SHORT_HISTORY = "short_history"`, `QUALITY_FCF_NOT_MEANINGFUL = "fcf_not_meaningful"`
  - `NormalizedDcfResult` frozen dataclass with fields: `normalized_base_fcf: float | None`, `reference_growth: float | None`, `normalized_intrinsic_value_per_share: float | None`, `normalized_upside_pct: float | None`, `implied_growth: float | None`, `plausibility_gap: float | None`, `valuation_quality: str`, `n_fcf_years: int`
  - `evaluate_normalized_dcf(*, annual_fcf, price, wacc, shares_outstanding, total_debt, cash, knobs=DEFAULT_DCF_KNOBS, window=NORMALIZED_FCF_WINDOW, min_years=MIN_NORMALIZED_FCF_YEARS) -> NormalizedDcfResult`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_normalized_dcf_math.py`:

```python
def _ok_kwargs(annual_fcf, price):
    return dict(annual_fcf=annual_fcf, price=price, wacc=0.10,
                shares_outstanding=10.0, total_debt=0.0, cash=0.0)


def test_evaluate_marks_financials_not_meaningful():
    from croesus.factors.equity.normalized import (
        QUALITY_FCF_NOT_MEANINGFUL, evaluate_normalized_dcf)
    res = evaluate_normalized_dcf(**_ok_kwargs([107.0, 13.0, -42.0, -148.0], 50.0))
    assert res.valuation_quality == QUALITY_FCF_NOT_MEANINGFUL
    assert res.normalized_intrinsic_value_per_share is None
    assert res.implied_growth is None


def test_evaluate_flags_short_history():
    from croesus.factors.equity.normalized import (
        QUALITY_SHORT_HISTORY, evaluate_normalized_dcf)
    res = evaluate_normalized_dcf(**_ok_kwargs([100.0, 104.0, 102.0], 50.0))
    assert res.valuation_quality == QUALITY_SHORT_HISTORY
    assert res.n_fcf_years == 3


def test_evaluate_full_result_ok():
    from croesus.factors.equity.normalized import QUALITY_OK, evaluate_normalized_dcf
    series = [100.0, 102.0, 101.0, 103.0, 102.0]  # ~flat, all positive, 5 yrs
    res = evaluate_normalized_dcf(**_ok_kwargs(series, 200.0))
    assert res.valuation_quality == QUALITY_OK
    assert res.normalized_base_fcf == pytest.approx(102.0)  # median
    assert res.normalized_intrinsic_value_per_share is not None
    assert res.implied_growth is not None
    # priced well above the ~flat normalized intrinsic -> positive plausibility gap
    assert res.plausibility_gap > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_normalized_dcf_math.py -k evaluate -v`
Expected: FAIL with `ImportError: cannot import name 'evaluate_normalized_dcf'`.

- [ ] **Step 3: Implement the assembler**

Append to `croesus/factors/equity/normalized.py` (add `from dataclasses import dataclass` at top):

```python
QUALITY_OK = "ok"
QUALITY_SHORT_HISTORY = "short_history"
QUALITY_FCF_NOT_MEANINGFUL = "fcf_not_meaningful"


@dataclass(frozen=True)
class NormalizedDcfResult:
    normalized_base_fcf: float | None
    reference_growth: float | None
    normalized_intrinsic_value_per_share: float | None
    normalized_upside_pct: float | None
    implied_growth: float | None
    plausibility_gap: float | None
    valuation_quality: str
    n_fcf_years: int


def evaluate_normalized_dcf(
    *,
    annual_fcf: list[float],
    price: float,
    wacc: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    knobs: DcfKnobs = DEFAULT_DCF_KNOBS,
    window: int = NORMALIZED_FCF_WINDOW,
    min_years: int = MIN_NORMALIZED_FCF_YEARS,
) -> NormalizedDcfResult:
    """One-shot normalized DCF: median base + log-linear reference growth +
    normalized forward intrinsic + reverse-DCF implied growth + plausibility gap.

    ``valuation_quality`` is ``fcf_not_meaningful`` when the normalized base or
    reference growth is undefined (financials / sign-flipping FCF), else
    ``short_history`` when fewer than ``min_years`` of FCF are available, else
    ``ok``. Returns a fully-populated result in every case (never raises).
    """
    n_years = len(annual_fcf[-window:])
    base = normalized_base_fcf(annual_fcf, window=window)
    growth = loglinear_fcf_growth(annual_fcf, window=window)
    if base is None or base <= 0 or growth is None:
        return NormalizedDcfResult(
            normalized_base_fcf=base, reference_growth=growth,
            normalized_intrinsic_value_per_share=None, normalized_upside_pct=None,
            implied_growth=None, plausibility_gap=None,
            valuation_quality=QUALITY_FCF_NOT_MEANINGFUL, n_fcf_years=n_years,
        )
    forward = two_stage_dcf(
        base_fcf=base, growth_rate=growth, wacc=wacc,
        shares_outstanding=shares_outstanding, total_debt=total_debt, cash=cash,
        knobs=knobs,
    )
    intrinsic = forward.intrinsic_value_per_share if forward else None
    upside = (intrinsic / price - 1.0) if (intrinsic is not None and price) else None
    implied = reverse_dcf_implied_growth(
        price=price, base_fcf=base, wacc=wacc,
        shares_outstanding=shares_outstanding, total_debt=total_debt, cash=cash,
        knobs=knobs,
    )
    gap = (implied - growth) if implied is not None else None
    quality = QUALITY_SHORT_HISTORY if n_years < min_years else QUALITY_OK
    return NormalizedDcfResult(
        normalized_base_fcf=base, reference_growth=growth,
        normalized_intrinsic_value_per_share=intrinsic, normalized_upside_pct=upside,
        implied_growth=implied, plausibility_gap=gap,
        valuation_quality=quality, n_fcf_years=n_years,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_normalized_dcf_math.py -v`
Expected: PASS (all math tests, 12 total).

- [ ] **Step 5: Commit**

```bash
git add croesus/factors/equity/normalized.py tests/test_normalized_dcf_math.py
git commit -m "✨ feat: evaluate_normalized_dcf assembler with valuation_quality flags"
```

---

### Task 5: Schema + repository for `normalized_dcf_snapshots`

**Files:**
- Modify: `croesus/db/schema.sql` (append new table)
- Create: `croesus/factors/equity/normalized_repository.py`
- Test: `tests/test_normalized_dcf_repository.py`

**Interfaces:**
- Produces:
  - `NormalizedDcfSnapshot` frozen dataclass: `asset_id: str`, `date: date`, `current_price: float | None`, `normalized_base_fcf: float | None`, `reference_growth: float | None`, `normalized_intrinsic_value_per_share: float | None`, `normalized_upside_pct: float | None`, `implied_growth: float | None`, `plausibility_gap: float | None`, `valuation_quality: str`, `n_fcf_years: int`, `wacc: float | None`, `assumptions: dict`
  - `NormalizedDcfRepository(conn)` with `upsert(snapshot) -> None`, `get(asset_id, as_of) -> NormalizedDcfSnapshot | None` (latest on/before `as_of`), `load_latest(as_of) -> list[NormalizedDcfSnapshot]` (each asset's most recent snapshot on/before `as_of`).

- [ ] **Step 1: Append the table to `schema.sql`**

Add at the end of `croesus/db/schema.sql`:

```sql
-- Normalized-FCF reverse-DCF methodology (separate from valuation_snapshots).
CREATE TABLE IF NOT EXISTS normalized_dcf_snapshots (
  asset_id                              TEXT NOT NULL,
  date                                  DATE NOT NULL,
  current_price                         DOUBLE,
  normalized_base_fcf                   DOUBLE,
  reference_growth                      DOUBLE,
  normalized_intrinsic_value_per_share  DOUBLE,
  normalized_upside_pct                 DOUBLE,
  implied_growth                        DOUBLE,
  plausibility_gap                      DOUBLE,
  valuation_quality                     TEXT,
  n_fcf_years                           INTEGER,
  wacc                                  DOUBLE,
  assumptions_json                      TEXT,
  created_at                            TIMESTAMP DEFAULT now(),
  updated_at                            TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, date)
);
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_normalized_dcf_repository.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.equity.normalized_repository import (
    NormalizedDcfRepository,
    NormalizedDcfSnapshot,
)


def _snap(asset_id: str, d: date, gap: float) -> NormalizedDcfSnapshot:
    return NormalizedDcfSnapshot(
        asset_id=asset_id, date=d, current_price=100.0,
        normalized_base_fcf=50.0, reference_growth=0.03,
        normalized_intrinsic_value_per_share=80.0, normalized_upside_pct=-0.2,
        implied_growth=0.25, plausibility_gap=gap, valuation_quality="ok",
        n_fcf_years=8, wacc=0.10, assumptions={"source": "model"},
    )


def test_upsert_and_get_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        repo = NormalizedDcfRepository(conn)
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.22))
        got = repo.get("US_EQ_AAPL", date(2026, 6, 30))
        assert got is not None
        assert got.plausibility_gap == 0.22
        assert got.assumptions["source"] == "model"


def test_upsert_overwrites_same_key(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        repo = NormalizedDcfRepository(conn)
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.22))
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.11))
        assert repo.get("US_EQ_AAPL", date(2026, 6, 30)).plausibility_gap == 0.11


def test_load_latest_one_row_per_asset(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        repo = NormalizedDcfRepository(conn)
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 3, 31), 0.5))
        repo.upsert(_snap("US_EQ_AAPL", date(2026, 6, 30), 0.2))
        repo.upsert(_snap("US_EQ_MSFT", date(2026, 6, 30), 0.3))
        rows = repo.load_latest(date(2026, 6, 30))
        by_asset = {r.asset_id: r for r in rows}
        assert set(by_asset) == {"US_EQ_AAPL", "US_EQ_MSFT"}
        assert by_asset["US_EQ_AAPL"].plausibility_gap == 0.2  # the June row, not March
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_normalized_dcf_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.factors.equity.normalized_repository'`.

- [ ] **Step 4: Implement the repository**

Create `croesus/factors/equity/normalized_repository.py` (mirror `ValuationSnapshotRepository` in `croesus/factors/equity/repository.py` for the JSON/`get` patterns):

```python
"""Persistence for the normalized-FCF reverse-DCF methodology."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

import duckdb

_COLUMNS = (
    "asset_id", "date", "current_price", "normalized_base_fcf", "reference_growth",
    "normalized_intrinsic_value_per_share", "normalized_upside_pct", "implied_growth",
    "plausibility_gap", "valuation_quality", "n_fcf_years", "wacc", "assumptions_json",
)


@dataclass(frozen=True)
class NormalizedDcfSnapshot:
    asset_id: str
    date: date
    current_price: float | None
    normalized_base_fcf: float | None
    reference_growth: float | None
    normalized_intrinsic_value_per_share: float | None
    normalized_upside_pct: float | None
    implied_growth: float | None
    plausibility_gap: float | None
    valuation_quality: str
    n_fcf_years: int
    wacc: float | None
    assumptions: dict = field(default_factory=dict)


class NormalizedDcfRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, snapshot: NormalizedDcfSnapshot) -> None:
        self.conn.execute(
            """
            INSERT INTO normalized_dcf_snapshots (
              asset_id, date, current_price, normalized_base_fcf, reference_growth,
              normalized_intrinsic_value_per_share, normalized_upside_pct,
              implied_growth, plausibility_gap, valuation_quality, n_fcf_years,
              wacc, assumptions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, date) DO UPDATE SET
              current_price = excluded.current_price,
              normalized_base_fcf = excluded.normalized_base_fcf,
              reference_growth = excluded.reference_growth,
              normalized_intrinsic_value_per_share = excluded.normalized_intrinsic_value_per_share,
              normalized_upside_pct = excluded.normalized_upside_pct,
              implied_growth = excluded.implied_growth,
              plausibility_gap = excluded.plausibility_gap,
              valuation_quality = excluded.valuation_quality,
              n_fcf_years = excluded.n_fcf_years,
              wacc = excluded.wacc,
              assumptions_json = excluded.assumptions_json,
              updated_at = now()
            """,
            [
                snapshot.asset_id, snapshot.date, snapshot.current_price,
                snapshot.normalized_base_fcf, snapshot.reference_growth,
                snapshot.normalized_intrinsic_value_per_share,
                snapshot.normalized_upside_pct, snapshot.implied_growth,
                snapshot.plausibility_gap, snapshot.valuation_quality,
                snapshot.n_fcf_years, snapshot.wacc,
                json.dumps(snapshot.assumptions),
            ],
        )

    def _row_to_snapshot(self, row: tuple) -> NormalizedDcfSnapshot:
        data = dict(zip(_COLUMNS, row))
        raw = data.pop("assumptions_json")
        assumptions = json.loads(raw) if isinstance(raw, str) else (raw or {})
        return NormalizedDcfSnapshot(assumptions=assumptions, **data)

    def get(self, asset_id: str, as_of: date) -> NormalizedDcfSnapshot | None:
        row = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM normalized_dcf_snapshots "
            "WHERE asset_id = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            [asset_id, as_of],
        ).fetchone()
        return None if row is None else self._row_to_snapshot(row)

    def load_latest(self, as_of: date) -> list[NormalizedDcfSnapshot]:
        rows = self.conn.execute(
            f"""
            WITH ranked AS (
                SELECT {', '.join(_COLUMNS)},
                       ROW_NUMBER() OVER (PARTITION BY asset_id ORDER BY date DESC) AS rn
                FROM normalized_dcf_snapshots
                WHERE date <= ?
            )
            SELECT {', '.join(_COLUMNS)} FROM ranked WHERE rn = 1
            """,
            [as_of],
        ).fetchall()
        return [self._row_to_snapshot(r) for r in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_normalized_dcf_repository.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add croesus/db/schema.sql croesus/factors/equity/normalized_repository.py tests/test_normalized_dcf_repository.py
git commit -m "🗃️ chore: add normalized_dcf_snapshots table and repository"
```

---

### Task 6: Orchestration — `compute_and_store_normalized_dcf`

**Files:**
- Create: `croesus/factors/equity/compute_normalized_dcf.py`
- Test: `tests/test_normalized_dcf_compute.py`

**Interfaces:**
- Consumes: `AssetRepository.list_active(asset_type="equity", country="US")`, `PriceRepository.load_daily_prices`, `FundamentalsRepository.get_annual_fcf` / `get_latest_metric` (METRIC_SHARES_OUTSTANDING / METRIC_TOTAL_DEBT / METRIC_CASH_AND_EQUIVALENTS), `ValuationSnapshotRepository.get(asset_id, as_of).wacc`, `evaluate_normalized_dcf`, `NormalizedDcfRepository.upsert`.
- Produces: `compute_and_store_normalized_dcf(conn, *, as_of: date | None = None, log: Callable[[str], None] = print) -> NormalizedDcfComputationResult` where `NormalizedDcfComputationResult` has `computed: list[str]`, `skipped: dict[str, str]`, `failed: dict[str, str]`.

**Design note:** WACC is **reused** from the mechanical `valuation_snapshots` row (the mechanical run always precedes this in the pipeline — Task 8). An asset with no mechanical snapshot for `as_of` is skipped with reason `"no mechanical wacc"`. This deliberately avoids recomputing beta and keeps the methodology additive.

- [ ] **Step 1: Write the failing test**

Create `tests/test_normalized_dcf_compute.py`:

```python
from __future__ import annotations

from datetime import date
from pathlib import Path

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.equity.compute_normalized_dcf import (
    compute_and_store_normalized_dcf,
)
from croesus.factors.equity.normalized_repository import NormalizedDcfRepository
from croesus.factors.equity.repository import (
    ValuationSnapshot,
    ValuationSnapshotRepository,
)
from croesus.fundamentals.repository import (
    METRIC_CASH_AND_EQUIVALENTS,
    METRIC_FREE_CASH_FLOW,
    METRIC_SHARES_OUTSTANDING,
    METRIC_TOTAL_DEBT,
    FundamentalMetric,
    FundamentalsRepository,
)
from croesus.prices.repository import PriceRepository


def _seed_asset(conn, asset_id="US_EQ_AAPL"):
    # 5 years of ~flat positive FCF, shares/debt/cash, a price, and a mechanical wacc.
    fr = FundamentalsRepository(conn)
    metrics = []
    for i, y in enumerate(range(2021, 2026)):
        metrics.append(FundamentalMetric(asset_id, date(y, 9, 30), "annual",
                                         METRIC_FREE_CASH_FLOW, 100.0e9 + i, "test"))
    for name, val in [(METRIC_SHARES_OUTSTANDING, 15.0e9),
                      (METRIC_TOTAL_DEBT, 100.0e9),
                      (METRIC_CASH_AND_EQUIVALENTS, 60.0e9)]:
        metrics.append(FundamentalMetric(asset_id, date(2025, 9, 30), "annual",
                                         name, val, "test"))
    fr.upsert_metrics(metrics)
    PriceRepository(conn).upsert_daily_prices(asset_id, [
        {"date": date(2026, 6, 30), "open": 200.0, "high": 200.0,
         "low": 200.0, "close": 200.0, "volume": 1_000_000}])
    ValuationSnapshotRepository(conn).upsert(ValuationSnapshot(
        asset_id=asset_id, date=date(2026, 6, 30),
        intrinsic_value_per_share=90.0, current_price=200.0, upside_pct=-0.55,
        wacc=0.10, fcf_growth_rate=0.01, terminal_growth_rate=0.025,
        assumptions={"source": "model"}))


def test_compute_persists_normalized_snapshot(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        seed_us_equities(conn)
        _seed_asset(conn)
        result = compute_and_store_normalized_dcf(
            conn, as_of=date(2026, 6, 30), log=lambda _m: None)
        assert "US_EQ_AAPL" in result.computed
        snap = NormalizedDcfRepository(conn).get("US_EQ_AAPL", date(2026, 6, 30))
        assert snap is not None
        assert snap.valuation_quality in {"ok", "short_history"}
        assert snap.implied_growth is not None
        assert snap.plausibility_gap is not None


def test_compute_skips_asset_without_mechanical_wacc(tmp_path: Path) -> None:
    db = tmp_path / "croesus.duckdb"
    migrate(db)
    with get_connection(db) as conn:
        seed_us_equities(conn)  # assets exist but no valuation_snapshots / fundamentals
        result = compute_and_store_normalized_dcf(
            conn, as_of=date(2026, 6, 30), log=lambda _m: None)
        assert result.computed == []
        assert all(reason for reason in result.skipped.values())
```

Confirm the exact constructor of `FundamentalMetric` and `PriceRepository.upsert_daily_prices` signatures before running (`grep -n "class FundamentalMetric\|def upsert_daily_prices" croesus/fundamentals/repository.py croesus/prices/repository.py`); adjust the test's seeding calls to match. The behavioral asserts stay the same.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_normalized_dcf_compute.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.factors.equity.compute_normalized_dcf'`.

- [ ] **Step 3: Implement the orchestration**

Create `croesus/factors/equity/compute_normalized_dcf.py`:

```python
"""Orchestration for the normalized-FCF reverse-DCF methodology.

Reads prices + cached fundamentals, REUSES the WACC the mechanical valuation
run already persisted (so beta is not recomputed), calls the pure math in
:mod:`croesus.factors.equity.normalized`, and writes one row per asset to
``normalized_dcf_snapshots``. Per-asset failures are logged and skipped — the
existing mechanical DCF and the rest of the run are never disturbed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

import duckdb
import pandas as pd

from croesus.assets.repository import AssetRepository
from croesus.factors.equity.normalized import evaluate_normalized_dcf
from croesus.factors.equity.normalized_repository import (
    NormalizedDcfRepository,
    NormalizedDcfSnapshot,
)
from croesus.factors.equity.repository import ValuationSnapshotRepository
from croesus.fundamentals.repository import (
    METRIC_CASH_AND_EQUIVALENTS,
    METRIC_SHARES_OUTSTANDING,
    METRIC_TOTAL_DEBT,
    FundamentalsRepository,
)
from croesus.prices.repository import PriceRepository


@dataclass(frozen=True)
class NormalizedDcfComputationResult:
    computed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


def _latest_close(frame: pd.DataFrame, as_of: date) -> float | None:
    if frame.empty:
        return None
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"]).dt.date
    data = data[data["date"] <= as_of].dropna(subset=["close"])
    if data.empty:
        return None
    return float(data.iloc[-1]["close"])


def compute_and_store_normalized_dcf(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of: date | None = None,
    log: Callable[[str], None] = print,
) -> NormalizedDcfComputationResult:
    as_of = as_of or date.today()
    assets = AssetRepository(conn).list_active(asset_type="equity", country="US")
    prices = PriceRepository(conn)
    fundamentals = FundamentalsRepository(conn)
    valuation_repo = ValuationSnapshotRepository(conn)
    out_repo = NormalizedDcfRepository(conn)
    result = NormalizedDcfComputationResult()

    for asset in assets:
        try:
            price = _latest_close(prices.load_daily_prices(asset.asset_id), as_of)
            if price is None:
                result.skipped[asset.asset_id] = "no price"
                continue
            snapshot = valuation_repo.get(asset.asset_id, as_of)
            if snapshot is None or snapshot.wacc is None:
                result.skipped[asset.asset_id] = "no mechanical wacc"
                continue
            annual_fcf = [v for _, v in fundamentals.get_annual_fcf(asset.asset_id)]
            if not annual_fcf:
                result.skipped[asset.asset_id] = "no FCF history"
                continue
            shares = fundamentals.get_latest_metric(
                asset.asset_id, METRIC_SHARES_OUTSTANDING)
            if not shares:
                result.skipped[asset.asset_id] = "no shares outstanding"
                continue
            total_debt = fundamentals.get_latest_metric(
                asset.asset_id, METRIC_TOTAL_DEBT)
            cash = fundamentals.get_latest_metric(
                asset.asset_id, METRIC_CASH_AND_EQUIVALENTS)

            evaluation = evaluate_normalized_dcf(
                annual_fcf=annual_fcf, price=price, wacc=snapshot.wacc,
                shares_outstanding=shares, total_debt=total_debt, cash=cash,
            )
            out_repo.upsert(NormalizedDcfSnapshot(
                asset_id=asset.asset_id, date=as_of, current_price=price,
                normalized_base_fcf=evaluation.normalized_base_fcf,
                reference_growth=evaluation.reference_growth,
                normalized_intrinsic_value_per_share=(
                    evaluation.normalized_intrinsic_value_per_share),
                normalized_upside_pct=evaluation.normalized_upside_pct,
                implied_growth=evaluation.implied_growth,
                plausibility_gap=evaluation.plausibility_gap,
                valuation_quality=evaluation.valuation_quality,
                n_fcf_years=evaluation.n_fcf_years, wacc=snapshot.wacc,
                assumptions={
                    "source": "model", "window": evaluation.n_fcf_years,
                    "total_debt": total_debt, "cash": cash, "shares": shares,
                },
            ))
            result.computed.append(asset.asset_id)
            log(f"normalized DCF stored for {asset.symbol}: "
                f"quality={evaluation.valuation_quality} "
                f"gap={evaluation.plausibility_gap}")
        except Exception as exc:  # noqa: BLE001 - per-asset isolation.
            result.failed[asset.asset_id] = str(exc)
            log(f"normalized DCF failed for {asset.symbol}: {exc}")
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_normalized_dcf_compute.py -v`
Expected: PASS (2 tests). Fix seeding-call signatures if a constructor mismatch surfaces.

- [ ] **Step 5: Commit**

```bash
git add croesus/factors/equity/compute_normalized_dcf.py tests/test_normalized_dcf_compute.py
git commit -m "✨ feat: compute & store normalized DCF snapshots (reuses mechanical WACC)"
```

---

### Task 7: Register methodology + review function + card fields

**Files:**
- Modify: `croesus/opportunities/selection.py` (registry)
- Modify: `croesus/opportunities/review.py` (`OpportunityCard` fields, new review function, dispatch)
- Test: `tests/test_opportunity_review.py`

**Interfaces:**
- Consumes: `NormalizedDcfRepository.load_latest`, `_asset_labels` (existing in `review.py`).
- Produces:
  - Registry key `"normalized_dcf"` (`available=True`).
  - `OpportunityCard` gains optional fields (defaults `None`): `normalized_intrinsic_value: float | None = None`, `normalized_upside_pct: float | None = None`, `reference_growth: float | None = None`, `implied_growth: float | None = None`, `plausibility_gap: float | None = None`, `valuation_quality: str | None = None`, `n_fcf_years: int | None = None`.
  - `_review_methodology_normalized_dcf(conn, *, methodology, as_of, limit) -> list[OpportunityCard]`, sorted by `plausibility_gap` ascending (cheapest first; `None` last).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_opportunity_review.py`:

```python
def test_normalized_dcf_methodology_ranks_by_plausibility_gap(tmp_path: Path) -> None:
    from croesus.factors.equity.normalized_repository import (
        NormalizedDcfRepository, NormalizedDcfSnapshot)
    from croesus.opportunities.review import run_opportunity_review

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)
        repo = NormalizedDcfRepository(conn)
        # AAPL cheap (gap -0.05), MSFT expensive (gap +0.30).
        for aid, gap in [("US_EQ_AAPL", -0.05), ("US_EQ_MSFT", 0.30)]:
            repo.upsert(NormalizedDcfSnapshot(
                asset_id=aid, date=date(2026, 6, 30), current_price=100.0,
                normalized_base_fcf=50.0, reference_growth=0.03,
                normalized_intrinsic_value_per_share=110.0,
                normalized_upside_pct=0.10, implied_growth=0.03 + gap,
                plausibility_gap=gap, valuation_quality="ok", n_fcf_years=8,
                wacc=0.10, assumptions={}))
        result = run_opportunity_review(
            conn, methodology_key="normalized_dcf", as_of_date=date(2026, 6, 30),
            apply_risk_gate=False)
        symbols = [c.symbol for c in result.cards]
        assert symbols[0] == "AAPL"  # smaller gap ranks first
        assert result.cards[0].plausibility_gap == -0.05
        assert result.cards[0].valuation_quality == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_opportunity_review.py::test_normalized_dcf_methodology_ranks_by_plausibility_gap -v`
Expected: FAIL — methodology `normalized_dcf` is unknown / unavailable.

- [ ] **Step 3: Register the methodology**

In `croesus/opportunities/selection.py`, add to `OPPORTUNITY_METHODOLOGIES` (after `moat_adjusted_intrinsic_value`):

```python
    "normalized_dcf": OpportunityMethodology(
        key="normalized_dcf",
        label="Normalized reverse DCF",
        description=(
            "Methodology C: median-normalized FCF + log-linear growth + reverse "
            "DCF. Ranks by plausibility gap (market-implied growth vs FCF trend)."
        ),
        available=True,
    ),
```

- [ ] **Step 4: Add optional fields to `OpportunityCard`**

In `croesus/opportunities/review.py`, append to the `OpportunityCard` dataclass (after `bear_case`, keeping `risk_gate` last):

```python
    normalized_intrinsic_value: float | None = None
    normalized_upside_pct: float | None = None
    reference_growth: float | None = None
    implied_growth: float | None = None
    plausibility_gap: float | None = None
    valuation_quality: str | None = None
    n_fcf_years: int | None = None
```

- [ ] **Step 5: Add the review function and dispatch**

In `croesus/opportunities/review.py`, add the import near the top:

```python
from croesus.factors.equity.normalized_repository import NormalizedDcfRepository
```

Add the sort key and review function (next to `_opportunity_card_sort_key` / `_review_methodology_a`):

```python
def _normalized_card_sort_key(card: OpportunityCard) -> tuple[int, float, str]:
    if card.plausibility_gap is None:
        return (1, 0.0, card.symbol)
    return (0, card.plausibility_gap, card.symbol)  # ascending: cheapest first


def _review_methodology_normalized_dcf(
    conn: duckdb.DuckDBPyConnection,
    *,
    methodology: OpportunityMethodology,
    as_of: date,
    limit: int,
) -> list[OpportunityCard]:
    snapshots = NormalizedDcfRepository(conn).load_latest(as_of)
    labels = _asset_labels(conn, [s.asset_id for s in snapshots])
    cards: list[OpportunityCard] = []
    for snap in snapshots:
        symbol, name = labels.get(snap.asset_id, (snap.asset_id, None))
        cards.append(OpportunityCard(
            asset_id=snap.asset_id, symbol=symbol, name=name,
            methodology_key=methodology.key, as_of_date=snap.date,
            current_price=snap.current_price,
            mechanical_intrinsic_value=None, mechanical_upside_pct=None,
            band_intrinsic_by_scenario={}, band_upside_by_scenario={},
            base_upside_pct=None,
            thesis_as_of_date=None, thesis_confidence=None, evidence_source=None,
            moat_grade=None, tech_grade=None, sector_grade=None,
            disruption_grade=None, moat_evidence=None, tech_evidence=None,
            sector_evidence=None, disruption_evidence=None, bear_case=None,
            normalized_intrinsic_value=snap.normalized_intrinsic_value_per_share,
            normalized_upside_pct=snap.normalized_upside_pct,
            reference_growth=snap.reference_growth,
            implied_growth=snap.implied_growth,
            plausibility_gap=snap.plausibility_gap,
            valuation_quality=snap.valuation_quality,
            n_fcf_years=snap.n_fcf_years,
        ))
    cards.sort(key=_normalized_card_sort_key)
    return cards[:limit]
```

Then extend the dispatch in `run_opportunity_review`:

```python
    if methodology.key == "moat_adjusted_intrinsic_value":
        cards = _review_methodology_a(
            conn, methodology=methodology, as_of=as_of, limit=limit
        )
    elif methodology.key == "normalized_dcf":
        cards = _review_methodology_normalized_dcf(
            conn, methodology=methodology, as_of=as_of, limit=limit
        )
    else:  # pragma: no cover - guarded by select_methodology
        cards = []
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_opportunity_review.py -v`
Expected: PASS (new test + existing methodology-A tests unchanged).

- [ ] **Step 7: Commit**

```bash
git add croesus/opportunities/selection.py croesus/opportunities/review.py tests/test_opportunity_review.py
git commit -m "✨ feat: normalized_dcf opportunity methodology ranked by plausibility gap"
```

---

### Task 8: Wire compute into the pipeline (quarterly run + web scheduler)

**Files:**
- Modify: `croesus/jobs/quarterly_run.py` (after `compute_and_store_valuation_factors`)
- Modify: `croesus/web/scheduler.py` (after the `include_dcf=True` valuation call, ~line 92)
- Test: `tests/test_quarterly_run.py` (extend if present; else add a minimal job test)

**Interfaces:**
- Consumes: `compute_and_store_normalized_dcf` (Task 6).
- Produces: a populated `normalized_dcf_snapshots` table after each quarterly cadence.

- [ ] **Step 1: Write/extend the failing test**

Locate the quarterly-run test (`grep -rln "quarterly_run\|_run_quarterly" tests`). Add an assertion that, after the quarterly job runs against a seeded DB with fundamentals + a price, `normalized_dcf_snapshots` is non-empty:

```python
def test_quarterly_run_populates_normalized_dcf(tmp_path: Path) -> None:
    # ... reuse the existing quarterly-run fixture/seed in this file ...
    # after running the quarterly job against db_path:
    with get_connection(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM normalized_dcf_snapshots").fetchone()[0]
        assert n >= 1
```

If no quarterly-run test file exists, create `tests/test_normalized_dcf_pipeline.py` that calls the quarterly entry point directly with a seeded DB (mirror the seeding in `tests/test_normalized_dcf_compute.py`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -k "quarterly_run_populates_normalized_dcf or normalized_dcf_pipeline" -v`
Expected: FAIL (`normalized_dcf_snapshots` empty — compute not wired).

- [ ] **Step 3: Wire the quarterly job**

In `croesus/jobs/quarterly_run.py`, add the import and call the compute immediately after the mechanical `valuation_result = compute_and_store_valuation_factors(...)` line (same `conn`, same `as_of`):

```python
from croesus.factors.equity.compute_normalized_dcf import (
    compute_and_store_normalized_dcf,
)

# ... after valuation_result = compute_and_store_valuation_factors(...):
normalized_result = compute_and_store_normalized_dcf(conn, as_of=as_of, log=log)
```

Match the surrounding `as_of` / `log` variable names already in scope in that function. If the function logs a summary, append the normalized counts (`len(normalized_result.computed)`).

- [ ] **Step 4: Wire the web scheduler**

In `croesus/web/scheduler.py`, add the import (alongside the `compute_and_store_valuation_factors` import at line 65) and call it right after the `compute_and_store_valuation_factors(conn, include_dcf=True, ...)` call (~line 92):

```python
from croesus.factors.equity.compute_normalized_dcf import (
    compute_and_store_normalized_dcf,
)

# ... after the include_dcf=True valuation call:
compute_and_store_normalized_dcf(conn, log=lambda _m: None)
```

- [ ] **Step 5: Run the test + full suite**

Run: `pytest -k normalized -v && pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add croesus/jobs/quarterly_run.py croesus/web/scheduler.py tests/
git commit -m "✨ feat: run normalized DCF after mechanical valuation in quarterly pipeline"
```

---

### Task 9: Render normalized fields in the opportunity report

**Files:**
- Modify: `croesus/reports/opportunity.py`
- Test: `tests/test_opportunity_report.py` (or wherever the report is tested — `grep -rln "reports.opportunity\|opportunity report" tests`)

**Interfaces:**
- Consumes: the new `OpportunityCard` fields from Task 7.
- Produces: a report section that, for `methodology_key == "normalized_dcf"` cards, shows `reference_growth`, `implied_growth`, `plausibility_gap`, `normalized_upside_pct`, and a `valuation_quality` badge — and clearly labels the mechanical value as a "conservative FCF floor" where shown.

- [ ] **Step 1: Write the failing test**

```python
def test_report_shows_plausibility_gap_for_normalized_cards():
    from croesus.opportunities.review import OpportunityCard, OpportunityReviewResult
    from croesus.opportunities.selection import OPPORTUNITY_METHODOLOGIES
    from croesus.reports.opportunity import render_opportunity_report  # confirm name
    from datetime import date
    card = OpportunityCard(
        asset_id="US_EQ_AAPL", symbol="AAPL", name="Apple",
        methodology_key="normalized_dcf", as_of_date=date(2026, 6, 30),
        current_price=281.74, mechanical_intrinsic_value=None,
        mechanical_upside_pct=None, band_intrinsic_by_scenario={},
        band_upside_by_scenario={}, base_upside_pct=None, thesis_as_of_date=None,
        thesis_confidence=None, evidence_source=None, moat_grade=None,
        tech_grade=None, sector_grade=None, disruption_grade=None,
        moat_evidence=None, tech_evidence=None, sector_evidence=None,
        disruption_evidence=None, bear_case=None,
        normalized_intrinsic_value=54.0, normalized_upside_pct=-0.81,
        reference_growth=-0.027, implied_growth=0.20, plausibility_gap=0.227,
        valuation_quality="ok", n_fcf_years=4)
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["normalized_dcf"],
        as_of_date=date(2026, 6, 30), cards=[card])
    text = render_opportunity_report(result)  # confirm exact function name/signature
    assert "AAPL" in text
    assert "22.7" in text or "0.227" in text  # plausibility gap surfaced
```

Confirm the report module's public function name and signature first (`grep -n "def render\|def build\|def write" croesus/reports/opportunity.py`) and adjust the import/call to match. Keep the behavioral assert (gap is surfaced).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest -k report_shows_plausibility_gap -v`
Expected: FAIL (gap not rendered).

- [ ] **Step 3: Add normalized rendering**

In `croesus/reports/opportunity.py`, in the per-card rendering, branch on `card.methodology_key == "normalized_dcf"` (or simply render the normalized fields whenever `card.plausibility_gap is not None`). Add lines such as:

```python
if card.plausibility_gap is not None:
    lines.append(
        f"  implied growth {card.implied_growth:.1%} vs FCF trend "
        f"{card.reference_growth:.1%}  ->  plausibility gap "
        f"{card.plausibility_gap * 100:.1f} pts  [{card.valuation_quality}]"
    )
    if card.normalized_upside_pct is not None:
        lines.append(
            f"  normalized FCF floor upside {card.normalized_upside_pct:.1%} "
            f"(conservative floor, not fair value)"
        )
```

Match the report's existing line-buffer variable and formatting idiom (it may build a list of strings, a table, or a template — follow the established pattern in the file).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -k report_shows_plausibility_gap -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add croesus/reports/opportunity.py tests/
git commit -m "✨ feat: render plausibility gap and normalized FCF floor in opportunity report"
```

---

### Task 10: Documentation + graphify refresh

**Files:**
- Modify: `docs/` (add a short methodology note — match existing `docs/` structure; e.g. `docs/methodologies/normalized-dcf.md`)
- Run: graphify update

- [ ] **Step 1: Write the methodology doc**

Create a concise doc describing: the design rationale (AAPL worked example from this plan), the math (median base, log-linear growth, reverse DCF, plausibility gap), the `valuation_quality` flags, the coexistence-with-Methodology-A contract, and the forward-test intent. Reuse the "Design rationale" section of this plan.

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: PASS (whole suite).

- [ ] **Step 3: Refresh graphify + commit**

```bash
graphify update . || true
git add docs/ graphify-out/ || git add docs/
git commit -m "📝 docs: document the normalized reverse-DCF opportunity methodology"
```

---

## Deferred / Out of scope for this plan (explicit, not silent)

These were discussed but are intentionally **not** in this plan — each is its own future plan:

- **Thesis-driven reference growth** (#57): mapping moat/sector grades into a deterministic growth floor/boost on `reference_growth`. v1 keeps `reference_growth` pure FCF math (decision: "(a) normalized FCF 기반").
- **Composite multi-factor ranking** (#57): blending DCF upside + FCF yield + sector percentile + quality + thesis confidence + risk gate into one score. The `plausibility_gap` sort is this methodology's native signal; a cross-methodology composite is separate.
- **BRK-B share-class correction** (#55): B↔A ratio normalization + a generalized implied-vs-statement share sanity check. Independent bug fix; shares the `valuation_quality` flag concept introduced here.
- **Promotion workflow** (#56): `review_only → watch → approved_for_action_review → rejected` state machine. Governance layer on top of trustworthy numbers.
- **Web UI rendering** of the normalized methodology (detail page cards, methodology selector in the dashboard). This plan ships CLI + report; the web detail rendering is a follow-up.
- **10-year FCF backfill reliability**: yfinance may still return only ~4 years for many tickers (Task 1 is best-effort). A dedicated multi-source fundamentals backfill is a separate data-quality effort; until then the `short_history` flag carries the limitation.

---

## Self-Review

- **Spec coverage:** 10y FCF ingest → Task 1. Normalized base + growth → Task 2. Reverse DCF → Task 3. Quality flags + assembler → Task 4. Persistence → Task 5. Orchestration reusing mechanical WACC → Task 6. Separate methodology + ranking → Task 7. Pipeline wiring → Task 8. Report → Task 9. Docs → Task 10. "Keep mechanical DCF untouched" → Global Constraints + Task 6 design note + Task 7 (additive card fields/dispatch). ✅
- **Placeholder scan:** Every code step contains real code. Three tasks (1, 6, 9) ask the implementer to confirm an exact existing signature before running and adjust the seeding/import call — these are verification steps with named `grep` commands, not content placeholders; the behavioral asserts are fully specified.
- **Type consistency:** `NormalizedDcfResult` (math, Task 4) and `NormalizedDcfSnapshot` (persistence, Task 5) are deliberately distinct types; Task 6 maps one to the other field-by-field. `evaluate_normalized_dcf` keyword args match between Task 4 definition and Task 6 call site. `reverse_dcf_implied_growth` / `two_stage_dcf` keyword args match between Tasks 3/4 and `valuation.py`. `OpportunityCard` new fields (Task 7) match the `_review_methodology_normalized_dcf` constructor and the report reads (Task 9). Registry key `"normalized_dcf"` is identical across selection.py, review.py dispatch, and both test files. ✅
