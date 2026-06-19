# Phase C3: Thesis-Grade Intrinsic-Value Bands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Map an asset's latest structural-thesis grade (C2's `thesis_grades`) into DCF knobs, compute a **bear/base/bull** moat-adjusted intrinsic-value band, and persist it to a new `intrinsic_value_bands` table — WITHOUT changing the mechanical base valuation that the risk-management screener consumes.

**Architecture:** A pure grade→`DcfKnobs` mapping (`thesis_knobs.py`) drives three scenario knob-sets per asset (base = grade-mapped; bear/bull = one grade-step pessimistic/optimistic, clamped to the table's value range). A pure band computer (`intrinsic_bands.py`) reuses the existing `value_with_knobs` to value each scenario. The band rides along inside the existing quarterly DCF pass (`_compute_dcf`) as a **best-effort, grade-only** addition: it is computed and persisted ONLY for assets that have a `generated` thesis grade, and its failure never disturbs the base DCF. The base `valuation_snapshots` and the `price_to_intrinsic` factor stay computed from `DEFAULT_DCF_KNOBS` exactly as before.

**Key design decisions (user-confirmed 2026-06-20):**
- **Separate band, base untouched.** Grade-derived DCF goes ONLY to `intrinsic_value_bands`. `valuation_snapshots` / `price_to_intrinsic` (the risk-gate signal feeding screening + rebalance) keep using `DEFAULT_DCF_KNOBS`. The opportunity engine stays recommendation-only; the LLM thesis never flows into automatic rebalancing. **Line 269's `knobs = DEFAULT_DCF_KNOBS` is NOT changed.**
- **Band rule = one grade-step perturbation.** base = grade-mapped knobs; **bear** = moat & sector one step pessimistic + disruption one step worse (more risk premium); **bull** = the mirror; all steps clamped to the defined grade vocabulary.
- **Grade-only bands.** A band is produced only when a `generated` thesis grade exists for the asset (the event-prefiltered C2 shortlist) — never a universe-wide mechanical band.
- **`compute_fcf_growth` window stays fixed** (observed history); the moat-stretched CAP (`knobs.explicit_years`) controls only the projection length, not the historical-CAGR look-back. Growth is identical across the three scenarios (it is an observed fact, not a scenario lever); scenarios differ only in CAP / terminal growth / risk premium.

**Tech Stack:** Python, DuckDB, the existing `croesus/factors/equity/valuation.py` (`DcfKnobs`, `value_with_knobs`, `DcfResult`), C2's `ThesisGradeRepository`.

---

## File Structure

- Create: `croesus/factors/equity/thesis_knobs.py` — grade→`DcfKnobs` mapping + scenario perturbation.
- Modify: `croesus/research/thesis_repository.py` — add `load_latest_for_asset`.
- Create: `croesus/factors/equity/intrinsic_bands.py` — `SCENARIOS`, `ScenarioBand`, `compute_intrinsic_bands`.
- Modify: `croesus/db/schema.sql` — add `intrinsic_value_bands` table.
- Create: `croesus/factors/equity/band_repository.py` — `IntrinsicValueBandRepository`.
- Modify: `croesus/factors/equity/compute_valuation.py` — best-effort band step inside `_compute_dcf`; resolve the `compute_fcf_growth` NOTE.
- Modify: `croesus/factors/equity/valuation.py` — update the `compute_fcf_growth` NOTE to record the Phase C decision.
- Test: `tests/test_intrinsic_bands.py` — all unit + integration tests.

**No `local_sync` change.** The band is computed inside the existing quarterly DCF pass (`_run_quarterly` → `run_quarterly_pipeline` → `compute_and_store_valuation_factors(include_dcf=True)`), so it needs no new job or domain.

---

### Task 1: Grade → DcfKnobs mapping + scenario perturbation

**Files:**
- Create: `croesus/factors/equity/thesis_knobs.py`
- Test: `tests/test_intrinsic_bands.py`

**Context:** The three mapping tables are fixed by spec §방법론 A. Each graded dimension is an ordered scale; the scenario steps an index toward pessimism/optimism and clamps. For moat and sector, "optimistic" = higher index (more CAP years / higher terminal growth). For disruption, "optimistic" = LOWER risk (less premium), so its step direction is inverted. A `None` grade falls back to the default level so the base scenario of an ungraded dimension reproduces `DEFAULT_DCF_KNOBS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_intrinsic_bands.py
def test_scenario_knobs_base_maps_grades() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs

    base = scenario_knobs(moat="narrow", sector="stable", disruption="medium", scenario="base")
    assert base.explicit_years == 7          # CAP_YEARS["narrow"]
    assert base.terminal_growth_rate == 0.025  # TERMINAL_GROWTH["stable"]
    assert base.wacc_risk_premium == 0.01    # RISK_PREMIUM["medium"]


def test_scenario_knobs_bear_and_bull_step_one_notch() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs

    bear = scenario_knobs(moat="narrow", sector="stable", disruption="medium", scenario="bear")
    # moat narrow->none (CAP 5), sector stable->declining (0.015), disruption medium->high (0.02)
    assert bear.explicit_years == 5
    assert bear.terminal_growth_rate == 0.015
    assert bear.wacc_risk_premium == 0.02

    bull = scenario_knobs(moat="narrow", sector="stable", disruption="medium", scenario="bull")
    # moat narrow->wide (CAP 10), sector stable->secular_growth (0.030), disruption medium->low (0.0)
    assert bull.explicit_years == 10
    assert bull.terminal_growth_rate == 0.030
    assert bull.wacc_risk_premium == 0.0


def test_scenario_knobs_clamps_at_ends() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs

    # wide moat can't get wider; low disruption can't get safer.
    bull = scenario_knobs(moat="wide", sector="secular_growth", disruption="low", scenario="bull")
    assert bull.explicit_years == 10 and bull.terminal_growth_rate == 0.030
    assert bull.wacc_risk_premium == 0.0
    bear = scenario_knobs(moat="none", sector="declining", disruption="high", scenario="bear")
    assert bear.explicit_years == 5 and bear.terminal_growth_rate == 0.015
    assert bear.wacc_risk_premium == 0.02


def test_scenario_knobs_none_grade_falls_back_to_defaults() -> None:
    from croesus.factors.equity.thesis_knobs import scenario_knobs
    from croesus.factors.equity.valuation import DEFAULT_DCF_KNOBS

    base = scenario_knobs(moat=None, sector=None, disruption=None, scenario="base")
    assert base.explicit_years == DEFAULT_DCF_KNOBS.explicit_years          # none -> 5
    assert base.terminal_growth_rate == DEFAULT_DCF_KNOBS.terminal_growth_rate  # stable -> 0.025
    assert base.wacc_risk_premium == DEFAULT_DCF_KNOBS.wacc_risk_premium    # low -> 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_intrinsic_bands.py -k scenario_knobs -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# croesus/factors/equity/thesis_knobs.py
from __future__ import annotations

from croesus.factors.equity.valuation import DcfKnobs

# Spec §방법론 A mapping tables (keys are the C2 grade vocabularies).
CAP_YEARS = {"wide": 10, "narrow": 7, "none": 5}
TERMINAL_GROWTH = {"secular_growth": 0.030, "stable": 0.025, "declining": 0.015}
RISK_PREMIUM = {"low": 0.00, "medium": 0.01, "high": 0.02}

# Ordered worst->best for moat/sector (best = more CAP / higher terminal growth).
_MOAT_ORDER = ("none", "narrow", "wide")
_SECTOR_ORDER = ("declining", "stable", "secular_growth")
# Ordered low->high RISK for disruption (more risk = higher premium = worse).
_DISRUPTION_ORDER = ("low", "medium", "high")

# Default level per dimension when no grade is present — reproduces DEFAULT_DCF_KNOBS.
_DEFAULT_MOAT = "none"
_DEFAULT_SECTOR = "stable"
_DEFAULT_DISRUPTION = "low"

# Scenario step: bear pessimistic, bull optimistic (in moat/sector index terms).
_STEP = {"bear": -1, "base": 0, "bull": +1}


def _step(order: tuple[str, ...], level: str | None, delta: int, default: str) -> str:
    """Move ``level`` ``delta`` positions along ``order``, clamped to its ends."""
    current = level if level in order else default
    idx = order.index(current)
    clamped = max(0, min(len(order) - 1, idx + delta))
    return order[clamped]


def scenario_knobs(
    *, moat: str | None, sector: str | None, disruption: str | None, scenario: str
) -> DcfKnobs:
    """Map a thesis grade to a scenario's DCF knobs.

    base = grade-mapped knobs; bear/bull step every dimension one notch toward
    pessimism/optimism (disruption inverted: pessimism = more risk premium),
    clamped to the grade vocabulary.
    """
    delta = _STEP[scenario]
    moat_lvl = _step(_MOAT_ORDER, moat, delta, _DEFAULT_MOAT)
    sector_lvl = _step(_SECTOR_ORDER, sector, delta, _DEFAULT_SECTOR)
    # Optimism = LESS disruption risk, so step disruption opposite to delta.
    disruption_lvl = _step(_DISRUPTION_ORDER, disruption, -delta, _DEFAULT_DISRUPTION)
    return DcfKnobs(
        explicit_years=CAP_YEARS[moat_lvl],
        terminal_growth_rate=TERMINAL_GROWTH[sector_lvl],
        wacc_risk_premium=RISK_PREMIUM[disruption_lvl],
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_intrinsic_bands.py -k scenario_knobs -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/factors/equity/thesis_knobs.py tests/test_intrinsic_bands.py
git commit -m "✨ feat: add grade→DcfKnobs mapping with scenario perturbation (C3)"
```

---

### Task 2: `ThesisGradeRepository.load_latest_for_asset`

**Files:**
- Modify: `croesus/research/thesis_repository.py`
- Test: `tests/test_intrinsic_bands.py`

**Context:** C2's `load_for_asset` matches an EXACT `(asset_id, as_of_date)`. The quarterly DCF runs on its own `as_of` (often a different date than the grader's latest event cohort), so C3 needs "the most recent `generated` grade on or before `as_of`" — mirroring `ValuationSnapshotRepository.get`'s range scan.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_intrinsic_bands.py
from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_load_latest_for_asset_returns_most_recent_generated(tmp_path: Path) -> None:
    from croesus.research.thesis_models import (
        STATUS_FAILED,
        STATUS_GENERATED,
        ThesisGrade,
    )
    from croesus.research.thesis_repository import ThesisGradeRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    def _grade(d: date, status: str, moat: str | None) -> ThesisGrade:
        return ThesisGrade(
            asset_id="US_EQ_AAPL", as_of_date=d, run_id="r", model="m",
            status=status, moat_grade=moat, sector_grade="stable",
            disruption_grade="low",
        )

    with get_connection(db_path) as conn:
        repo = ThesisGradeRepository(conn)
        repo.upsert(_grade(date(2026, 5, 1), STATUS_GENERATED, "narrow"))
        repo.upsert(_grade(date(2026, 6, 1), STATUS_GENERATED, "wide"))

        latest = repo.load_latest_for_asset("US_EQ_AAPL", date(2026, 6, 19))
        assert latest is not None and latest.moat_grade == "wide"
        # Range-bounded: nothing on or before an earlier date than the first grade.
        assert repo.load_latest_for_asset("US_EQ_AAPL", date(2026, 1, 1)) is None
        # A failed grade is ignored even if it's the most recent.
        repo.upsert(_grade(date(2026, 6, 10), STATUS_FAILED, None))
        still = repo.load_latest_for_asset("US_EQ_AAPL", date(2026, 6, 19))
        assert still.as_of_date == date(2026, 6, 1) and still.moat_grade == "wide"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_intrinsic_bands.py -k load_latest -v`
Expected: FAIL with `AttributeError: 'ThesisGradeRepository' object has no attribute 'load_latest_for_asset'`

- [ ] **Step 3: Write minimal implementation**

Add this method to `ThesisGradeRepository` (after `load_for_asset`). `_COLUMNS` and the `ThesisGrade` reconstruction already exist in the class — reuse them:

```python
    def load_latest_for_asset(self, asset_id: str, as_of: date) -> ThesisGrade | None:
        """Most recent ``generated`` grade on or before ``as_of`` (point-in-time)."""
        row = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM thesis_grades "
            "WHERE asset_id = ? AND as_of_date <= ? AND status = 'generated' "
            "ORDER BY as_of_date DESC LIMIT 1",
            [asset_id, as_of],
        ).fetchone()
        if row is None:
            return None
        data = dict(zip(_COLUMNS, row))
        meta = data.pop("metadata")
        return ThesisGrade(
            metadata=json.loads(meta) if isinstance(meta, str) else (meta or {}),
            **data,
        )
```

(`json` is already imported in `thesis_repository.py`; `STATUS_GENERATED`'s literal `'generated'` is used directly in SQL to match the existing string-literal style in the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_intrinsic_bands.py -k load_latest -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/research/thesis_repository.py tests/test_intrinsic_bands.py
git commit -m "✨ feat: add load_latest_for_asset point-in-time grade lookup (C3)"
```

---

### Task 3: Pure band computer

**Files:**
- Create: `croesus/factors/equity/intrinsic_bands.py`
- Test: `tests/test_intrinsic_bands.py`

**Context:** Given the shared DCF inputs (the SAME base_fcf/growth/rf/beta/shares/debt/cash the base DCF used) and a thesis grade, value all three scenarios by calling the existing `value_with_knobs` with each scenario's knobs. Growth is identical across scenarios (observed history, not a scenario lever). Returns one `ScenarioBand` per scenario (a scenario may be `None` if its knobs make the DCF invalid, e.g. WACC ≤ terminal growth).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_intrinsic_bands.py
def test_compute_intrinsic_bands_orders_bear_base_bull() -> None:
    from croesus.factors.equity.intrinsic_bands import (
        SCENARIOS,
        compute_intrinsic_bands,
    )

    bands = compute_intrinsic_bands(
        base_fcf=1.0e9, growth=0.08, risk_free_rate=0.045, beta=1.0,
        shares_outstanding=1.0e8, total_debt=0.0, cash=0.0,
        moat="narrow", sector="stable", disruption="medium",
    )
    assert set(bands) == set(SCENARIOS) == {"bear", "base", "bull"}
    # A wider moat / higher terminal / lower premium must not value LOWER than bear.
    iv = {s: b.intrinsic_value_per_share for s, b in bands.items() if b is not None}
    assert iv["bull"] >= iv["base"] >= iv["bear"]
    # Knobs are recorded per scenario for persistence/audit.
    assert bands["bull"].explicit_years == 10
    assert bands["bear"].wacc_risk_premium == 0.02
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_intrinsic_bands.py -k compute_intrinsic_bands -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# croesus/factors/equity/intrinsic_bands.py
from __future__ import annotations

from dataclasses import dataclass

from croesus.factors.equity.thesis_knobs import scenario_knobs
from croesus.factors.equity.valuation import value_with_knobs

SCENARIOS = ("bear", "base", "bull")


@dataclass(frozen=True)
class ScenarioBand:
    scenario: str
    intrinsic_value_per_share: float
    wacc: float
    fcf_growth_rate: float
    terminal_growth_rate: float
    explicit_years: int
    wacc_risk_premium: float


def compute_intrinsic_bands(
    *,
    base_fcf: float,
    growth: float,
    risk_free_rate: float,
    beta: float,
    shares_outstanding: float,
    total_debt: float | None,
    cash: float | None,
    moat: str | None,
    sector: str | None,
    disruption: str | None,
) -> dict[str, ScenarioBand | None]:
    """Value bear/base/bull scenarios from one thesis grade.

    Growth is shared across scenarios (an observed fact); scenarios differ only
    in CAP / terminal growth / risk premium via ``scenario_knobs``. A scenario is
    ``None`` when its knobs make the DCF invalid (e.g. WACC <= terminal growth).
    """
    bands: dict[str, ScenarioBand | None] = {}
    for scenario in SCENARIOS:
        knobs = scenario_knobs(
            moat=moat, sector=sector, disruption=disruption, scenario=scenario
        )
        dcf = value_with_knobs(
            base_fcf=base_fcf,
            growth_rate=growth,
            risk_free_rate=risk_free_rate,
            beta=beta,
            shares_outstanding=shares_outstanding,
            total_debt=total_debt,
            cash=cash,
            knobs=knobs,
        )
        bands[scenario] = (
            None
            if dcf is None
            else ScenarioBand(
                scenario=scenario,
                intrinsic_value_per_share=dcf.intrinsic_value_per_share,
                wacc=dcf.wacc,
                fcf_growth_rate=dcf.fcf_growth_rate,
                terminal_growth_rate=dcf.terminal_growth_rate,
                explicit_years=knobs.explicit_years,
                wacc_risk_premium=knobs.wacc_risk_premium,
            )
        )
    return bands
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_intrinsic_bands.py -k compute_intrinsic_bands -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/factors/equity/intrinsic_bands.py tests/test_intrinsic_bands.py
git commit -m "✨ feat: add pure bear/base/bull intrinsic-value band computer (C3)"
```

---

### Task 4: `intrinsic_value_bands` table + repository

**Files:**
- Modify: `croesus/db/schema.sql`
- Create: `croesus/factors/equity/band_repository.py`
- Test: `tests/test_intrinsic_bands.py`

**Context:** One row per `(asset_id, date, scenario)`. Stores the scenario's intrinsic value + upside + the knobs used + the thesis provenance (which grade date/run drove it), so a human can audit exactly why bull says what it says. Idempotent upsert (re-running the quarterly DCF overwrites).

- [ ] **Step 1: Add the table to `croesus/db/schema.sql`**

Append after the `thesis_grades` table:

```sql
-- Phase C3 (opportunity engine): moat-adjusted intrinsic-value band. Three rows
-- per (asset_id, date) — bear/base/bull — driven by the asset's latest thesis
-- grade via the fixed CAP/terminal/risk-premium tables. This is the opportunity
-- engine's recommendation-only output; it does NOT feed price_to_intrinsic or
-- the risk-management screener (those keep the mechanical DEFAULT_DCF_KNOBS).
CREATE TABLE IF NOT EXISTS intrinsic_value_bands (
  asset_id                  TEXT NOT NULL,
  date                      DATE NOT NULL,
  scenario                  TEXT NOT NULL,   -- 'bear' | 'base' | 'bull'
  intrinsic_value_per_share DOUBLE,
  current_price             DOUBLE,
  upside_pct                DOUBLE,
  wacc                      DOUBLE,
  fcf_growth_rate           DOUBLE,
  terminal_growth_rate      DOUBLE,
  explicit_years            INTEGER,
  wacc_risk_premium         DOUBLE,
  moat_grade                TEXT,
  sector_grade              TEXT,
  disruption_grade          TEXT,
  thesis_as_of_date         DATE,            -- which grade drove this band
  thesis_run_id             TEXT,
  created_at                TIMESTAMP DEFAULT now(),
  updated_at                TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, date, scenario)
);
```

- [ ] **Step 2: Write the failing test**

```python
# add to tests/test_intrinsic_bands.py
def test_band_repository_upserts_three_scenarios(tmp_path: Path) -> None:
    from croesus.factors.equity.band_repository import (
        BandRow,
        IntrinsicValueBandRepository,
    )

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    asof = date(2026, 6, 19)

    def _row(scenario: str, iv: float) -> BandRow:
        return BandRow(
            asset_id="US_EQ_AAPL", date=asof, scenario=scenario,
            intrinsic_value_per_share=iv, current_price=100.0,
            upside_pct=iv / 100.0 - 1.0, wacc=0.09, fcf_growth_rate=0.08,
            terminal_growth_rate=0.025, explicit_years=7, wacc_risk_premium=0.01,
            moat_grade="narrow", sector_grade="stable", disruption_grade="medium",
            thesis_as_of_date=asof, thesis_run_id="run-1",
        )

    with get_connection(db_path) as conn:
        repo = IntrinsicValueBandRepository(conn)
        repo.upsert_band(_row("bear", 80.0))
        repo.upsert_band(_row("base", 120.0))
        repo.upsert_band(_row("bull", 160.0))
        # Re-grade overwrites in place.
        repo.upsert_band(_row("base", 130.0))

        rows = repo.load_for_asset("US_EQ_AAPL", asof)
        by_scenario = {r.scenario: r for r in rows}
    assert set(by_scenario) == {"bear", "base", "bull"}
    assert by_scenario["base"].intrinsic_value_per_share == 130.0
    assert by_scenario["bull"].explicit_years == 7
    assert by_scenario["bear"].moat_grade == "narrow"
    assert len(rows) == 3  # idempotent: base overwritten, not duplicated
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_intrinsic_bands.py -k band_repository -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 4: Write minimal implementation**

```python
# croesus/factors/equity/band_repository.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import duckdb

_COLUMNS = (
    "asset_id", "date", "scenario", "intrinsic_value_per_share", "current_price",
    "upside_pct", "wacc", "fcf_growth_rate", "terminal_growth_rate",
    "explicit_years", "wacc_risk_premium", "moat_grade", "sector_grade",
    "disruption_grade", "thesis_as_of_date", "thesis_run_id",
)


@dataclass(frozen=True)
class BandRow:
    asset_id: str
    date: date
    scenario: str
    intrinsic_value_per_share: float | None
    current_price: float | None
    upside_pct: float | None
    wacc: float | None
    fcf_growth_rate: float | None
    terminal_growth_rate: float | None
    explicit_years: int | None
    wacc_risk_premium: float | None
    moat_grade: str | None
    sector_grade: str | None
    disruption_grade: str | None
    thesis_as_of_date: date | None
    thesis_run_id: str | None


class IntrinsicValueBandRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_band(self, row: BandRow) -> None:
        self.conn.execute(
            """
            INSERT INTO intrinsic_value_bands (
              asset_id, date, scenario, intrinsic_value_per_share, current_price,
              upside_pct, wacc, fcf_growth_rate, terminal_growth_rate,
              explicit_years, wacc_risk_premium, moat_grade, sector_grade,
              disruption_grade, thesis_as_of_date, thesis_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, date, scenario) DO UPDATE SET
              intrinsic_value_per_share = excluded.intrinsic_value_per_share,
              current_price = excluded.current_price,
              upside_pct = excluded.upside_pct,
              wacc = excluded.wacc,
              fcf_growth_rate = excluded.fcf_growth_rate,
              terminal_growth_rate = excluded.terminal_growth_rate,
              explicit_years = excluded.explicit_years,
              wacc_risk_premium = excluded.wacc_risk_premium,
              moat_grade = excluded.moat_grade,
              sector_grade = excluded.sector_grade,
              disruption_grade = excluded.disruption_grade,
              thesis_as_of_date = excluded.thesis_as_of_date,
              thesis_run_id = excluded.thesis_run_id,
              updated_at = now()
            """,
            [
                row.asset_id, row.date, row.scenario, row.intrinsic_value_per_share,
                row.current_price, row.upside_pct, row.wacc, row.fcf_growth_rate,
                row.terminal_growth_rate, row.explicit_years, row.wacc_risk_premium,
                row.moat_grade, row.sector_grade, row.disruption_grade,
                row.thesis_as_of_date, row.thesis_run_id,
            ],
        )

    def load_for_asset(self, asset_id: str, as_of: date) -> list[BandRow]:
        rows = self.conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM intrinsic_value_bands "
            "WHERE asset_id = ? AND date = ? ORDER BY scenario",
            [asset_id, as_of],
        ).fetchall()
        return [BandRow(**dict(zip(_COLUMNS, r))) for r in rows]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_intrinsic_bands.py -k band_repository -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add croesus/db/schema.sql croesus/factors/equity/band_repository.py tests/test_intrinsic_bands.py
git commit -m "🗃️ feat: add intrinsic_value_bands table and repository (C3)"
```

---

### Task 5: Wire the band into the quarterly DCF pass (base untouched)

**Files:**
- Modify: `croesus/factors/equity/compute_valuation.py`
- Modify: `croesus/factors/equity/valuation.py` (NOTE only)
- Test: `tests/test_intrinsic_bands.py`

**Context:** Inside `_compute_dcf`, AFTER the existing base-DCF block (which is unchanged), add a best-effort band step: load the asset's latest `generated` grade; if there is none, do nothing (grade-only bands). If there is one, compute the three scenarios with `compute_intrinsic_bands` (reusing the same `base_fcf`/`growth`/`rf`/`beta`/`shares`/`debt`/`cash`) and upsert one `BandRow` per scenario. A band failure must NOT lose `price_to_intrinsic`, so wrap the whole band step in its own try/except. `_compute_dcf` gains two params — `band_repo` and `thesis_repo` — constructed once in the caller.

- [ ] **Step 1: Write the failing integration test**

This mirrors the proven seeding in `tests/test_valuation_job.py` (real `PriceRepository.upsert_daily_prices(df)` + `FundamentalMetric`/`upsert_metrics`, via `seed_us_equities`). Add to the top of `tests/test_intrinsic_bands.py` (alongside the existing imports):

```python
# add to tests/test_intrinsic_bands.py
import pandas as pd

_AS_OF_C3 = date(2026, 6, 1)


def _price_frame(close: float) -> pd.DataFrame:
    return pd.DataFrame([
        {"date": date(2026, 5, 29), "open": close, "high": close, "low": close,
         "close": close, "adjusted_close": close, "volume": 1000},
        {"date": _AS_OF_C3, "open": close, "high": close, "low": close,
         "close": close, "adjusted_close": close, "volume": 1000},
    ])


def _fcf_fundamentals(asset_id: str, fcf: list[float]):
    from croesus.fundamentals.repository import (
        METRIC_CASH_AND_EQUIVALENTS,
        METRIC_FREE_CASH_FLOW,
        METRIC_SHARES_OUTSTANDING,
        METRIC_TOTAL_DEBT,
        PERIOD_ANNUAL,
        FundamentalMetric,
    )

    years = [date(2022, 12, 31), date(2023, 12, 31), date(2024, 12, 31)]
    rows = [
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_TOTAL_DEBT, 0.0, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_CASH_AND_EQUIVALENTS, 0.0, "t"),
        FundamentalMetric(asset_id, years[-1], PERIOD_ANNUAL, METRIC_SHARES_OUTSTANDING, 10.0, "t"),
    ]
    for year, value in zip(years, fcf):
        rows.append(FundamentalMetric(asset_id, year, PERIOD_ANNUAL, METRIC_FREE_CASH_FLOW, value, "t"))
    return rows


def test_compute_valuation_writes_band_only_for_graded_assets(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.factors.equity.band_repository import IntrinsicValueBandRepository
    from croesus.factors.equity.compute_valuation import (
        compute_and_store_valuation_factors,
    )
    from croesus.factors.equity.repository import ValuationSnapshotRepository
    from croesus.fundamentals.repository import FundamentalsRepository
    from croesus.prices.repository import PriceRepository
    from croesus.research.thesis_models import STATUS_GENERATED, ThesisGrade
    from croesus.research.thesis_repository import ThesisGradeRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL, MSFT, NVDA (all US equities)
        prices = PriceRepository(conn)
        prices.upsert_daily_prices("US_EQ_AAPL", _price_frame(100.0), source="test")
        prices.upsert_daily_prices("US_EQ_MSFT", _price_frame(200.0), source="test")
        funds = FundamentalsRepository(conn)
        funds.upsert_metrics(_fcf_fundamentals("US_EQ_AAPL", [30.0, 40.0, 50.0]))
        funds.upsert_metrics(_fcf_fundamentals("US_EQ_MSFT", [40.0, 50.0, 60.0]))
        # AAPL is graded; MSFT is not.
        ThesisGradeRepository(conn).upsert(ThesisGrade(
            asset_id="US_EQ_AAPL", as_of_date=_AS_OF_C3, run_id="r", model="m",
            status=STATUS_GENERATED, moat_grade="wide", sector_grade="secular_growth",
            disruption_grade="low",
        ))

        compute_and_store_valuation_factors(conn, include_dcf=True, as_of=_AS_OF_C3)

        band_repo = IntrinsicValueBandRepository(conn)
        graded_bands = band_repo.load_for_asset("US_EQ_AAPL", _AS_OF_C3)
        ungraded_bands = band_repo.load_for_asset("US_EQ_MSFT", _AS_OF_C3)
        # The base valuation snapshot must still be the mechanical default-knob DCF.
        snap = ValuationSnapshotRepository(conn).get("US_EQ_AAPL", _AS_OF_C3)

    assert {b.scenario for b in graded_bands} == {"bear", "base", "bull"}
    assert ungraded_bands == []          # grade-only: no thesis -> no band
    # Base snapshot uses DEFAULT knobs (explicit_years 5), NOT the wide-moat 10.
    assert snap is not None and snap.assumptions["explicit_years"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_intrinsic_bands.py -k writes_band -v`
Expected: FAIL (bands table empty — wiring not present yet; likely an empty `graded_bands`)

- [ ] **Step 3: Add the band imports to `compute_valuation.py`**

Near the other `croesus.factors.equity` imports (after the `valuation` import block):

```python
from croesus.factors.equity.band_repository import (
    BandRow,
    IntrinsicValueBandRepository,
)
from croesus.factors.equity.intrinsic_bands import compute_intrinsic_bands
from croesus.research.thesis_repository import ThesisGradeRepository
```

- [ ] **Step 4: Construct the band/thesis repos in the caller and pass them through**

In `compute_and_store_valuation_factors`, alongside `snapshot_repo = ValuationSnapshotRepository(conn)` (line 120):

```python
    snapshot_repo = ValuationSnapshotRepository(conn)
    band_repo = IntrinsicValueBandRepository(conn)
    thesis_repo = ThesisGradeRepository(conn)
```

Update the `_compute_dcf` call (line 128-130) to pass them:

```python
                price_to_intrinsic = _compute_dcf(
                    calc, as_of, rf=rf, snapshot_repo=snapshot_repo,
                    band_repo=band_repo, thesis_repo=thesis_repo,
                    result=result, log=log,
                )
```

- [ ] **Step 5: Add the params to `_compute_dcf` and the best-effort band step**

Change the `_compute_dcf` signature (line 243-251) to add the two repos:

```python
def _compute_dcf(
    calc: _AssetCalc,
    as_of: date,
    *,
    rf: float,
    snapshot_repo: ValuationSnapshotRepository,
    band_repo: IntrinsicValueBandRepository,
    thesis_repo: ThesisGradeRepository,
    result: ValuationComputationResult,
    log: Callable[[str], None],
) -> float | None:
```

Then, immediately AFTER `result.dcf_computed.append(asset_id)` (line 309) and BEFORE the `if dcf.intrinsic_value_per_share <= 0:` return guard, insert the band step:

```python
    _store_intrinsic_bands(
        calc, as_of, rf=rf, beta=beta, growth=growth,
        thesis_repo=thesis_repo, band_repo=band_repo, log=log,
    )
```

Add the helper function below `_compute_dcf`:

```python
def _store_intrinsic_bands(
    calc: _AssetCalc,
    as_of: date,
    *,
    rf: float,
    beta: float,
    growth: float,
    thesis_repo: ThesisGradeRepository,
    band_repo: IntrinsicValueBandRepository,
    log: Callable[[str], None],
) -> None:
    """Best-effort moat-adjusted band for an asset WITH a thesis grade.

    Grade-only: ungraded assets get no band. Reuses the same DCF inputs as the
    base snapshot but with grade-derived scenario knobs. Its failure must never
    disturb the base DCF / price_to_intrinsic, so all of it is caught here.
    """
    asset_id = calc.asset.asset_id
    try:
        grade = thesis_repo.load_latest_for_asset(asset_id, as_of)
        if grade is None:
            return  # no thesis -> no band (recommendation-only, shortlist-only)
        bands = compute_intrinsic_bands(
            base_fcf=calc.annual_fcf[-1],
            growth=growth,
            risk_free_rate=rf,
            beta=beta,
            shares_outstanding=calc.shares or 0.0,
            total_debt=calc.fundamentals["total_debt"],
            cash=calc.fundamentals["cash_and_equivalents"],
            moat=grade.moat_grade,
            sector=grade.sector_grade,
            disruption=grade.disruption_grade,
        )
        for scenario, band in bands.items():
            if band is None:
                continue
            upside = (
                band.intrinsic_value_per_share / calc.price - 1.0
                if calc.price
                else None
            )
            band_repo.upsert_band(BandRow(
                asset_id=asset_id, date=as_of, scenario=scenario,
                intrinsic_value_per_share=band.intrinsic_value_per_share,
                current_price=calc.price, upside_pct=upside, wacc=band.wacc,
                fcf_growth_rate=band.fcf_growth_rate,
                terminal_growth_rate=band.terminal_growth_rate,
                explicit_years=band.explicit_years,
                wacc_risk_premium=band.wacc_risk_premium,
                moat_grade=grade.moat_grade, sector_grade=grade.sector_grade,
                disruption_grade=grade.disruption_grade,
                thesis_as_of_date=grade.as_of_date, thesis_run_id=grade.run_id,
            ))
    except Exception as exc:  # noqa: BLE001 - band is best-effort; base DCF stands.
        log(f"intrinsic band failed for {calc.asset.symbol}: {exc}")
```

- [ ] **Step 6: Resolve the `compute_fcf_growth` NOTE in `valuation.py`**

Replace the NOTE comment (the lines starting `# NOTE: this look-back window is pinned…` through the `Phase C … fixed history.` line) with the resolved decision:

```python
    # Phase C decision: the CAGR look-back stays a FIXED observed-history window
    # (DCF_EXPLICIT_YEARS). The moat-stretched CAP (knobs.explicit_years) controls
    # only how long that observed growth is PROJECTED, not how far back it is
    # measured — historical growth is an observed fact, not a thesis lever.
    recent = annual_fcf[-DCF_EXPLICIT_YEARS:]
```

- [ ] **Step 7: Run the integration test to verify it passes**

Run: `python -m pytest tests/test_intrinsic_bands.py -k writes_band -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add croesus/factors/equity/compute_valuation.py croesus/factors/equity/valuation.py tests/test_intrinsic_bands.py
git commit -m "✨ feat: compute grade-driven intrinsic band in quarterly DCF, base untouched (C3)"
```

---

### Task 6: Full regression + base-stability guard

- [ ] **Step 1: Run the entire suite**

Run: `python -m pytest -q`
Expected: PASS — all prior tests plus the new `test_intrinsic_bands.py`, zero regressions. Pay special attention to existing `tests/test_valuation*.py` — they must still pass unchanged, proving the base DCF / `price_to_intrinsic` path was not altered.

- [ ] **Step 2: Confirm the risk-gate factor is unchanged**

Run: `git diff main...HEAD -- croesus/factors/equity/compute_valuation.py`
Expected: the diff ADDS the band step + repo construction + the two `_compute_dcf` params, but does NOT change line 269's `knobs = DEFAULT_DCF_KNOBS`, the `value_with_knobs(...)` base call, the `ValuationSnapshot` upsert, or the `price_to_intrinsic` return. Verify by eye.

- [ ] **Step 3: Confirm the factor name is intact**

Run: `grep -rn "price_to_intrinsic" croesus/factors/equity/compute_valuation.py`
Expected: still present, still produced from the base DCF — not renamed, not rederived from a band.

---

## Self-Review

**Spec coverage:** §방법론 A mapping tables (CAP_YEARS/TERMINAL_GROWTH/RISK_PREMIUM) → Task 1 verbatim. `bear/base/bull` band output → Tasks 3-5. Grade→knob axis assignment (moat→CAP, sector→terminal, disruption→premium) → Task 1 `scenario_knobs`. The "band" the spec left open → user-confirmed one-step perturbation (Task 1) + separate-table persistence (Tasks 4-5).

**Decisions honored:** Separate band / base untouched → Task 5 leaves line 269 + the snapshot + `price_to_intrinsic` unchanged and asserts it (Task 6 Step 2-3). Grade-only bands → Task 5 `_store_intrinsic_bands` returns early when no grade (asserted in Task 5 test). `compute_fcf_growth` fixed window → Task 5 Step 6. One-step perturbation clamped → Task 1 tests.

**Type consistency:** `scenario_knobs` keyword args (`moat`/`sector`/`disruption`/`scenario`) match its callers in `compute_intrinsic_bands` (Task 3) and the test (Task 1). `ScenarioBand` fields (Task 3) are read into `BandRow` fields (Task 5) — both enumerate the same scenario/knob/dcf attributes. `BandRow` fields (Task 4) match `_COLUMNS` and the INSERT/SELECT (Task 4). `ThesisGrade.moat_grade`/`sector_grade`/`disruption_grade`/`as_of_date`/`run_id` (C2) are the exact fields `_store_intrinsic_bands` reads.

**Placeholder scan:** none — every step has complete code.

**No scope creep:** no `local_sync` change (band rides the existing quarterly DCF). No new factor. No rename. `valuation.py` change is comment-only.
