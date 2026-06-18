# Event-Driven Pre-Filter Implementation Plan (Phase B2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Scan the active equity universe with cheap, fully deterministic detectors and emit a candidate `events` set ("something forward just happened") — the sourcing funnel that later methodologies (A/B) apply LLM theses to.

**Architecture:** Pure, DB-free detector functions (mirroring `croesus/factors/common.py`'s compute-from-DataFrame style) take an asset's price history, latest valuation snapshot, and recent disclosures and return zero-or-more `Event` records. An orchestration job reads the DB per asset, runs the detectors, and upserts events. No LLM, no cross-sectional ranking — every signal is a per-asset time-series threshold we own. Wired into `local_sync` after `daily_run` (prices + valuation) with a soft trigger on `disclosures_run`.

**Tech Stack:** Python, DuckDB (`croesus.db`), pandas (already used by the factor engine). No new dependencies.

---

## Scope & Boundaries

- **In scope — four deterministic detectors** computable from data we already ingest:
  1. `abnormal_volume` — latest volume vs trailing-window mean/std (z-score), spikes only.
  2. `abnormal_return` — latest daily return vs trailing return volatility (σ-multiple), with direction.
  3. `recent_disclosure` — a SEC filing (esp. 8-K) filed within a short window of `as_of` (from B1's `disclosures`).
  4. `valuation_dislocation` — price far from DCF intrinsic, read straight off `valuation_snapshots.upside_pct`.
- **Deferred (need ingestion we don't have): ** `news_spike` (no news API yet) and `guidance_change` (B1 stored filing *metadata* only — no 8-K text). These are natural triggers once a text/news-ingestion phase lands; the `events` schema and detector registry are built to accept new `event_type`s without migration.
- **Out of scope:** any LLM thesis (that's methodologies A/B, Phase C/D); cross-sectional scoring/ranking; automatic portfolio influence (recommendation-only, per spec guardrails).

## Design Decisions (thresholds we own — tunable module constants)

| Constant | Value | Rationale |
|---|---|---|
| `VOLUME_WINDOW` / `VOLUME_Z_THRESHOLD` | 21 / 2.0 | ~1 trading month baseline; ≥2σ volume spike is a recognized attention signal. Spikes only (a volume *drop* is not an event). |
| `RETURN_WINDOW` / `RETURN_SIGMA_MULT` | 63 / 3.0 | ~3 months (matches `volatility_3m`); a ±3σ daily move is a genuine repricing. |
| `DISCLOSURE_WINDOW_DAYS` | 7 | A filing in the last week of `as_of` is "fresh news". |
| `VALUATION_DISLOCATION_PCT` | 0.25 | |intrinsic−price|/price ≥ 25% is a material gap worth a thesis. |

`Event.magnitude` is detector-specific (z-score, signed σ-multiple, days-ago, or upside fraction) and `Event.direction ∈ {up, down, neutral}`. Freshness for the `events` domain is keyed to the job's last success in `job_runs` (NOT `MAX(as_of_date)`), because a genuinely quiet scan writes zero rows and must not look perpetually stale — the same lesson applied to `disclosures` in B1.

## File Structure

| File | Responsibility |
|---|---|
| `croesus/events/__init__.py` | Package marker (empty). |
| `croesus/events/models.py` | `Event` frozen dataclass + `EventScanResult`; the `event_type`/`direction`/`source` string constants. |
| `croesus/events/detectors.py` | Pure detectors + `detect_events` aggregator. DB-free, unit-tested. |
| `croesus/events/repository.py` | `EventRepository.upsert` / `load_for_scan`. |
| `croesus/events/scan.py` | `run_event_scan(conn, *, as_of_date, log)` orchestration with per-asset isolation. |
| `croesus/db/schema.sql` | Append `events` table. |
| `croesus/jobs/run_status.py` | Add `DomainSpec` for the `events` domain. |
| `croesus/jobs/local_sync.py` | Add `_run_event_scan` runner + register the `SyncJob`. |
| `tests/test_events.py` | All unit/integration tests. |

---

### Task 1: `events` table schema

**Files:**
- Modify: `croesus/db/schema.sql` (append at end)
- Test: `tests/test_events.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_events.py`:

```python
from datetime import date
from pathlib import Path

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def test_migrate_creates_events_table(tmp_path: Path) -> None:
    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)
    with get_connection(db_path) as conn:
        cols = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'events'"
            ).fetchall()
        }
    assert cols == {
        "asset_id",
        "as_of_date",
        "event_type",
        "direction",
        "magnitude",
        "detail",
        "source",
        "created_at",
    }
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py::test_migrate_creates_events_table -v`
Expected: FAIL — empty column set (table absent).

- [ ] **Step 3: Append the table DDL**

Append to the end of `croesus/db/schema.sql`:

```sql
-- Phase B2 (opportunity engine): deterministic event-driven pre-filter output.
-- One row per (asset, as_of_date, event_type): a cheap "something forward just
-- happened" signal computed with NO LLM from prices, valuation, and disclosures.
-- This is the candidate funnel methodologies A/B later apply an LLM thesis to;
-- nothing here sizes or executes a trade. ``magnitude`` is detector-specific
-- (z-score / signed sigma-multiple / days-ago / upside fraction); ``direction``
-- is 'up' | 'down' | 'neutral'. New detectors add new ``event_type`` values
-- without a schema change.
CREATE TABLE IF NOT EXISTS events (
  asset_id    TEXT NOT NULL,
  as_of_date  DATE NOT NULL,
  event_type  TEXT NOT NULL,   -- 'abnormal_volume'|'abnormal_return'|'recent_disclosure'|'valuation_dislocation'
  direction   TEXT,            -- 'up' | 'down' | 'neutral'
  magnitude   DOUBLE,          -- detector-specific strength
  detail      TEXT,            -- human-readable one-liner
  source      TEXT NOT NULL,   -- source table: 'prices_daily'|'valuation_snapshots'|'disclosures'
  created_at  TIMESTAMP DEFAULT now(),
  PRIMARY KEY (asset_id, as_of_date, event_type)
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_events.py::test_migrate_creates_events_table -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/db/schema.sql tests/test_events.py
git commit -m "🗃️ chore: add events table for deterministic event pre-filter"
```

---

### Task 2: `Event` model and constants

**Files:**
- Create: `croesus/events/__init__.py`
- Create: `croesus/events/models.py`
- Test: `tests/test_events.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_events.py`:

```python
def test_event_model_and_constants() -> None:
    from croesus.events.models import (
        DIRECTION_UP,
        EVENT_ABNORMAL_VOLUME,
        Event,
        EventScanResult,
    )

    event = Event(
        asset_id="US_EQ_AAPL",
        as_of_date=date(2026, 6, 1),
        event_type=EVENT_ABNORMAL_VOLUME,
        direction=DIRECTION_UP,
        magnitude=3.2,
        detail="volume 3.2σ above 21d mean",
        source="prices_daily",
    )
    assert event.event_type == "abnormal_volume"
    assert event.direction == "up"

    result = EventScanResult()
    assert result.scanned == []
    assert result.events == []
    assert result.failed == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py::test_event_model_and_constants -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.events'`

- [ ] **Step 3: Create the package and models**

Create `croesus/events/__init__.py` (empty file).

Create `croesus/events/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Event types (extend freely; new detectors add new values, no schema change).
EVENT_ABNORMAL_VOLUME = "abnormal_volume"
EVENT_ABNORMAL_RETURN = "abnormal_return"
EVENT_RECENT_DISCLOSURE = "recent_disclosure"
EVENT_VALUATION_DISLOCATION = "valuation_dislocation"

# Directions.
DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_NEUTRAL = "neutral"

# Source tables (provenance).
SOURCE_PRICES = "prices_daily"
SOURCE_VALUATION = "valuation_snapshots"
SOURCE_DISCLOSURES = "disclosures"


@dataclass(frozen=True)
class Event:
    """A deterministic candidate signal for one asset on one date."""

    asset_id: str
    as_of_date: date
    event_type: str
    direction: str
    magnitude: float
    detail: str
    source: str


@dataclass(frozen=True)
class EventScanResult:
    scanned: list[str] = field(default_factory=list)         # symbols scanned
    events: list[Event] = field(default_factory=list)        # all events emitted
    failed: dict[str, str] = field(default_factory=dict)     # symbol -> error
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_events.py::test_event_model_and_constants -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/events/__init__.py croesus/events/models.py tests/test_events.py
git commit -m "✨ feat: add Event model and type constants for event pre-filter"
```

---

### Task 3: Price-based detectors (`abnormal_volume`, `abnormal_return`)

**Files:**
- Create: `croesus/events/detectors.py`
- Test: `tests/test_events.py` (add)

Pure functions over a price DataFrame with the columns `PriceRepository.load_daily_prices` returns (`date, open, high, low, close, adjusted_close, volume, source`). Numeric coercion mirrors `croesus/factors/common.py`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_events.py`:

```python
import pandas as pd


def _price_frame(closes: list[float], volumes: list[float]) -> pd.DataFrame:
    n = len(closes)
    start = date(2026, 1, 1)
    return pd.DataFrame(
        {
            "date": [start + pd.Timedelta(days=i) for i in range(n)],
            "close": closes,
            "volume": volumes,
        }
    )


def test_detect_abnormal_volume_flags_spike_only() -> None:
    from croesus.events.detectors import detect_abnormal_volume

    # 30 mildly-varying-volume days (mean ~1000, non-zero std), then a big spike.
    closes = [100.0] * 31
    base_vol = [900.0, 1000.0, 1100.0] * 10  # 30 values, mean 1000, std > 0
    volumes = base_vol + [5000.0]
    event = detect_abnormal_volume("US_EQ_AAPL", date(2026, 2, 1), _price_frame(closes, volumes))
    assert event is not None
    assert event.event_type == "abnormal_volume"
    assert event.direction == "up"
    assert event.magnitude > 2.0
    assert event.source == "prices_daily"

    # A volume DROP is not an event.
    drop = detect_abnormal_volume(
        "US_EQ_AAPL", date(2026, 2, 1), _price_frame(closes, base_vol + [10.0])
    )
    assert drop is None

    # Perfectly flat volume -> zero std -> no event (no divide-by-zero).
    flat = detect_abnormal_volume(
        "US_EQ_AAPL", date(2026, 2, 1), _price_frame(closes, [1000.0] * 31)
    )
    assert flat is None

    # Too little history -> None.
    short = detect_abnormal_volume(
        "US_EQ_AAPL", date(2026, 2, 1), _price_frame([100.0] * 5, [1000.0] * 5)
    )
    assert short is None


def test_detect_abnormal_return_flags_direction() -> None:
    from croesus.events.detectors import detect_abnormal_return

    # 64 days of tiny ±0.1% wiggles, then a +20% jump on the last day.
    closes = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(64)]
    closes.append(closes[-1] * 1.20)
    volumes = [1000.0] * len(closes)
    up = detect_abnormal_return("US_EQ_AAPL", date(2026, 3, 1), _price_frame(closes, volumes))
    assert up is not None
    assert up.event_type == "abnormal_return"
    assert up.direction == "up"
    assert up.magnitude > 3.0

    # A -20% crash flags 'down' with negative magnitude.
    closes_down = closes[:-1] + [closes[-2] * 0.80]
    down = detect_abnormal_return(
        "US_EQ_AAPL", date(2026, 3, 1), _price_frame(closes_down, volumes)
    )
    assert down is not None
    assert down.direction == "down"
    assert down.magnitude < -3.0

    # Calm series -> no event.
    calm = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(65)]
    assert detect_abnormal_return(
        "US_EQ_AAPL", date(2026, 3, 1), _price_frame(calm, volumes)
    ) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_events.py -k "abnormal" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.events.detectors'`

- [ ] **Step 3: Implement the price detectors**

Create `croesus/events/detectors.py`:

```python
from __future__ import annotations

from datetime import date

import pandas as pd

from croesus.events.models import (
    DIRECTION_DOWN,
    DIRECTION_UP,
    EVENT_ABNORMAL_RETURN,
    EVENT_ABNORMAL_VOLUME,
    SOURCE_PRICES,
    Event,
)

VOLUME_WINDOW = 21
VOLUME_Z_THRESHOLD = 2.0
RETURN_WINDOW = 63
RETURN_SIGMA_MULT = 3.0


def _clean_prices(prices: pd.DataFrame) -> pd.DataFrame:
    data = prices.sort_values("date").copy()
    data["close"] = pd.to_numeric(data["close"], errors="coerce")
    data["volume"] = pd.to_numeric(data["volume"], errors="coerce")
    return data.dropna(subset=["close", "volume"])


def detect_abnormal_volume(
    asset_id: str, as_of_date: date, prices: pd.DataFrame
) -> Event | None:
    """Latest volume ≥ VOLUME_Z_THRESHOLD σ above the trailing window mean.

    Spikes only — an unusually *low*-volume day is not a forward signal.
    """
    data = _clean_prices(prices)
    if len(data) < VOLUME_WINDOW + 1:
        return None
    volume = data["volume"]
    latest = float(volume.iloc[-1])
    baseline = volume.iloc[-(VOLUME_WINDOW + 1):-1]
    mean = float(baseline.mean())
    std = float(baseline.std())
    if std == 0:
        return None
    z = (latest - mean) / std
    if z < VOLUME_Z_THRESHOLD:
        return None
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_ABNORMAL_VOLUME,
        direction=DIRECTION_UP,
        magnitude=z,
        detail=f"volume {z:.1f}σ above {VOLUME_WINDOW}d mean",
        source=SOURCE_PRICES,
    )


def detect_abnormal_return(
    asset_id: str, as_of_date: date, prices: pd.DataFrame
) -> Event | None:
    """Latest daily return ≥ RETURN_SIGMA_MULT × trailing return volatility."""
    data = _clean_prices(prices)
    returns = data["close"].pct_change().dropna()
    if len(returns) < RETURN_WINDOW + 1:
        return None
    latest = float(returns.iloc[-1])
    baseline = returns.iloc[-(RETURN_WINDOW + 1):-1]
    sigma = float(baseline.std())
    if sigma == 0:
        return None
    sigma_mult = latest / sigma
    if abs(sigma_mult) < RETURN_SIGMA_MULT:
        return None
    direction = DIRECTION_UP if latest > 0 else DIRECTION_DOWN
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_ABNORMAL_RETURN,
        direction=direction,
        magnitude=sigma_mult,
        detail=f"return {latest:+.1%} = {sigma_mult:+.1f}σ vs {RETURN_WINDOW}d vol",
        source=SOURCE_PRICES,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_events.py -k "abnormal" -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/events/detectors.py tests/test_events.py
git commit -m "✨ feat: add abnormal-volume and abnormal-return detectors"
```

---

### Task 4: Disclosure and valuation detectors + `detect_events` aggregator

**Files:**
- Modify: `croesus/events/detectors.py`
- Test: `tests/test_events.py` (add)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_events.py`:

```python
def test_detect_recent_disclosure_within_window() -> None:
    from croesus.disclosures.models import Disclosure
    from croesus.events.detectors import detect_recent_disclosure

    def _d(form: str, filed: date) -> Disclosure:
        return Disclosure(
            asset_id="US_EQ_AAPL",
            accession_number=f"{form}-{filed}",
            form_type=form,
            filed_date=filed,
            report_date=None,
            primary_doc_url=None,
            title=None,
        )

    as_of = date(2026, 6, 10)
    # An 8-K filed 3 days ago is within the 7-day window -> event.
    recent = detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("8-K", date(2026, 6, 7))])
    assert recent is not None
    assert recent.event_type == "recent_disclosure"
    assert recent.direction == "neutral"
    assert recent.magnitude == 3.0  # days ago
    assert "8-K" in recent.detail
    assert recent.source == "disclosures"

    # A filing 30 days ago is outside the window -> None.
    assert detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("10-K", date(2026, 5, 1))]) is None

    # Future-dated filing (> as_of) is ignored -> None.
    assert detect_recent_disclosure("US_EQ_AAPL", as_of, [_d("8-K", date(2026, 6, 20))]) is None

    # No filings -> None.
    assert detect_recent_disclosure("US_EQ_AAPL", as_of, []) is None


def test_detect_valuation_dislocation_direction_and_threshold() -> None:
    from croesus.factors.equity.repository import ValuationSnapshot
    from croesus.events.detectors import detect_valuation_dislocation

    def _snap(upside: float | None) -> ValuationSnapshot:
        return ValuationSnapshot(
            asset_id="US_EQ_AAPL",
            date=date(2026, 6, 1),
            intrinsic_value_per_share=120.0,
            current_price=100.0,
            upside_pct=upside,
            wacc=0.09,
            fcf_growth_rate=0.1,
            terminal_growth_rate=0.025,
            assumptions={},
        )

    as_of = date(2026, 6, 1)
    # +40% upside (price well below intrinsic) -> 'up' dislocation.
    under = detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(0.40))
    assert under is not None
    assert under.direction == "up"
    assert under.magnitude == 0.40
    assert under.source == "valuation_snapshots"

    # -40% (price above intrinsic) -> 'down'.
    over = detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(-0.40))
    assert over is not None and over.direction == "down"

    # Within ±25% band -> None.
    assert detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(0.10)) is None
    # Missing snapshot or upside -> None.
    assert detect_valuation_dislocation("US_EQ_AAPL", as_of, None) is None
    assert detect_valuation_dislocation("US_EQ_AAPL", as_of, _snap(None)) is None


def test_detect_events_aggregates_all_detectors() -> None:
    from croesus.disclosures.models import Disclosure
    from croesus.factors.equity.repository import ValuationSnapshot
    from croesus.events.detectors import detect_events

    # Mild ±0.1% wiggle (non-zero baseline std) then a +30% jump; varied volume
    # then a spike — so both price detectors have a real baseline to fire against.
    wiggle = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(64)]
    closes = wiggle + [wiggle[-1] * 1.30]                 # +30% jump
    volumes = ([900.0, 1000.0, 1100.0] * 22)[:64] + [9000.0]   # 64 varied + spike
    prices = _price_frame(closes, volumes)
    snapshot = ValuationSnapshot(
        asset_id="US_EQ_AAPL", date=date(2026, 3, 5), intrinsic_value_per_share=150.0,
        current_price=100.0, upside_pct=0.50, wacc=0.09, fcf_growth_rate=0.1,
        terminal_growth_rate=0.025, assumptions={},
    )
    disclosures = [
        Disclosure(
            asset_id="US_EQ_AAPL", accession_number="8-K-1", form_type="8-K",
            filed_date=date(2026, 3, 4), report_date=None, primary_doc_url=None, title=None,
        )
    ]
    as_of = date(2026, 3, 5)

    events = detect_events("US_EQ_AAPL", as_of, prices, snapshot, disclosures)
    types = {e.event_type for e in events}
    assert types == {
        "abnormal_volume",
        "abnormal_return",
        "recent_disclosure",
        "valuation_dislocation",
    }
    # A calm asset with no snapshot and no disclosures yields nothing.
    calm = _price_frame([100.0] * 65, [1000.0] * 65)
    assert detect_events("US_EQ_AAPL", as_of, calm, None, []) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_events.py -k "disclosure or dislocation or aggregates" -v`
Expected: FAIL with `ImportError: cannot import name 'detect_recent_disclosure'`

- [ ] **Step 3: Implement the remaining detectors and aggregator**

Append to `croesus/events/detectors.py` (add imports to the existing `from croesus.events.models import (...)` block: `DIRECTION_NEUTRAL`, `EVENT_RECENT_DISCLOSURE`, `EVENT_VALUATION_DISLOCATION`, `SOURCE_DISCLOSURES`, `SOURCE_VALUATION`; and new top-level imports for the types):

```python
# --- add to the module's imports ---
from croesus.disclosures.models import Disclosure
from croesus.events.models import (
    DIRECTION_NEUTRAL,
    EVENT_RECENT_DISCLOSURE,
    EVENT_VALUATION_DISLOCATION,
    SOURCE_DISCLOSURES,
    SOURCE_VALUATION,
)
from croesus.factors.equity.repository import ValuationSnapshot

# --- add to the module's constants ---
DISCLOSURE_WINDOW_DAYS = 7
VALUATION_DISLOCATION_PCT = 0.25


def detect_recent_disclosure(
    asset_id: str, as_of_date: date, disclosures: list[Disclosure]
) -> Event | None:
    """A filing dated within DISCLOSURE_WINDOW_DAYS at or before ``as_of_date``.

    Picks the most recent qualifying filing; the filing's existence is the
    signal (direction 'neutral' — reading intent is the LLM's job downstream).
    """
    in_window = [
        d
        for d in disclosures
        if d.filed_date <= as_of_date
        and (as_of_date - d.filed_date).days <= DISCLOSURE_WINDOW_DAYS
    ]
    if not in_window:
        return None
    most_recent = max(in_window, key=lambda d: d.filed_date)
    days_ago = (as_of_date - most_recent.filed_date).days
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_RECENT_DISCLOSURE,
        direction=DIRECTION_NEUTRAL,
        magnitude=float(days_ago),
        detail=f"{most_recent.form_type} filed {days_ago}d ago",
        source=SOURCE_DISCLOSURES,
    )


def detect_valuation_dislocation(
    asset_id: str, as_of_date: date, snapshot: ValuationSnapshot | None
) -> Event | None:
    """|upside_pct| ≥ VALUATION_DISLOCATION_PCT, read off the DCF snapshot.

    ``upside_pct`` > 0 means price is below intrinsic (an 'up' dislocation).
    """
    if snapshot is None or snapshot.upside_pct is None:
        return None
    upside = snapshot.upside_pct
    if abs(upside) < VALUATION_DISLOCATION_PCT:
        return None
    direction = DIRECTION_UP if upside > 0 else DIRECTION_DOWN
    return Event(
        asset_id=asset_id,
        as_of_date=as_of_date,
        event_type=EVENT_VALUATION_DISLOCATION,
        direction=direction,
        magnitude=upside,
        detail=f"price {upside:+.0%} vs DCF intrinsic",
        source=SOURCE_VALUATION,
    )


def detect_events(
    asset_id: str,
    as_of_date: date,
    prices: pd.DataFrame,
    snapshot: ValuationSnapshot | None,
    disclosures: list[Disclosure],
) -> list[Event]:
    """Run every detector for one asset; return the events that fired."""
    candidates = [
        detect_abnormal_volume(asset_id, as_of_date, prices),
        detect_abnormal_return(asset_id, as_of_date, prices),
        detect_recent_disclosure(asset_id, as_of_date, disclosures),
        detect_valuation_dislocation(asset_id, as_of_date, snapshot),
    ]
    return [e for e in candidates if e is not None]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_events.py -k "disclosure or dislocation or aggregates" -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/events/detectors.py tests/test_events.py
git commit -m "✨ feat: add disclosure + valuation detectors and detect_events aggregator"
```

---

### Task 5: `EventRepository`

**Files:**
- Create: `croesus/events/repository.py`
- Test: `tests/test_events.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_events.py`:

```python
def test_event_repository_upserts_idempotently(tmp_path: Path) -> None:
    from croesus.events.models import Event
    from croesus.events.repository import EventRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    as_of = date(2026, 6, 1)
    first = Event(
        asset_id="US_EQ_AAPL", as_of_date=as_of, event_type="abnormal_volume",
        direction="up", magnitude=2.5, detail="v2.5", source="prices_daily",
    )
    with get_connection(db_path) as conn:
        repo = EventRepository(conn)
        assert repo.upsert([first]) == 1
        # Re-scan same (asset, date, type) with a refined magnitude -> still one row.
        updated = Event(
            asset_id="US_EQ_AAPL", as_of_date=as_of, event_type="abnormal_volume",
            direction="up", magnitude=3.1, detail="v3.1", source="prices_daily",
        )
        assert repo.upsert([updated]) == 1
        rows = conn.execute(
            "SELECT asset_id, event_type, magnitude FROM events"
        ).fetchall()
        assert rows == [("US_EQ_AAPL", "abnormal_volume", 3.1)]

        loaded = repo.load_for_date("US_EQ_AAPL", as_of)
        assert len(loaded) == 1
        assert loaded[0].magnitude == 3.1
        assert loaded[0].source == "prices_daily"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py::test_event_repository_upserts_idempotently -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.events.repository'`

- [ ] **Step 3: Implement the repository**

Create `croesus/events/repository.py`:

```python
from __future__ import annotations

from datetime import date

import duckdb

from croesus.events.models import Event


class EventRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert(self, events: list[Event]) -> int:
        """Insert/update events keyed by (asset_id, as_of_date, event_type).

        Idempotent: re-scanning a date overwrites the mutable fields instead of
        duplicating rows. Returns the number of rows submitted.
        """
        if not events:
            return 0
        rows = [
            (
                e.asset_id,
                e.as_of_date,
                e.event_type,
                e.direction,
                e.magnitude,
                e.detail,
                e.source,
            )
            for e in events
        ]
        self.conn.executemany(
            """
            INSERT INTO events (
              asset_id, as_of_date, event_type, direction, magnitude, detail, source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (asset_id, as_of_date, event_type) DO UPDATE SET
              direction = excluded.direction,
              magnitude = excluded.magnitude,
              detail = excluded.detail,
              source = excluded.source
            """,
            rows,
        )
        return len(rows)

    def load_for_date(self, asset_id: str, as_of_date: date) -> list[Event]:
        """Events for one asset on one date (used by downstream methodologies)."""
        result = self.conn.execute(
            """
            SELECT asset_id, as_of_date, event_type, direction, magnitude, detail, source
            FROM events
            WHERE asset_id = ? AND as_of_date = ?
            ORDER BY event_type
            """,
            [asset_id, as_of_date],
        ).fetchall()
        return [
            Event(
                asset_id=row[0],
                as_of_date=row[1],
                event_type=row[2],
                direction=row[3],
                magnitude=row[4],
                detail=row[5],
                source=row[6],
            )
            for row in result
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_events.py::test_event_repository_upserts_idempotently -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/events/repository.py tests/test_events.py
git commit -m "✨ feat: add EventRepository with idempotent upsert"
```

---

### Task 6: `run_event_scan` orchestration job

**Files:**
- Create: `croesus/events/scan.py`
- Test: `tests/test_events.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_events.py`:

```python
def test_run_event_scan_emits_and_persists_events(tmp_path: Path) -> None:
    from croesus.assets.seed_us_equities import seed_us_equities
    from croesus.events.scan import run_event_scan
    from croesus.prices.repository import PriceRepository

    db_path = tmp_path / "croesus.duckdb"
    migrate(db_path)

    # AAPL gets a volume spike + return jump on the last day; MSFT/NVDA stay calm.
    # Baselines wiggle (non-zero std) so the price detectors have something to
    # fire against — a constant baseline yields std 0 and (correctly) no event.
    n = 65
    base_dates = [date(2026, 1, 1) + pd.Timedelta(days=i) for i in range(n)]
    wiggle = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(n - 1)]
    spike_close = wiggle + [wiggle[-1] * 1.30]                 # +30% jump
    varied_vol = ([900.0, 1000.0, 1100.0] * 22)[:n]           # 65 mildly-varied
    spike_vol = varied_vol[: n - 1] + [9000.0]                # spike on last day
    calm_close = [100.0 * (1.0 + 0.001 * ((-1) ** i)) for i in range(n)]
    calm_vol = varied_vol

    def _frame(closes, vols):
        return pd.DataFrame(
            {
                "date": base_dates,
                "open": closes,
                "high": closes,
                "low": closes,
                "close": closes,
                "adjusted_close": closes,
                "volume": vols,
            }
        )

    with get_connection(db_path) as conn:
        seed_us_equities(conn)  # AAPL, MSFT, NVDA
        prices = PriceRepository(conn)
        prices.upsert_daily_prices("US_EQ_AAPL", _frame(spike_close, spike_vol), source="test")
        prices.upsert_daily_prices("US_EQ_MSFT", _frame(calm_close, calm_vol), source="test")
        prices.upsert_daily_prices("US_EQ_NVDA", _frame(calm_close, calm_vol), source="test")

        result = run_event_scan(conn, as_of_date=date(2026, 3, 6))
        stored = conn.execute(
            "SELECT asset_id, event_type FROM events ORDER BY asset_id, event_type"
        ).fetchall()

    assert set(result.scanned) == {"AAPL", "MSFT", "NVDA"}
    assert result.failed == {}
    # Only AAPL fired (volume + return); calm names produced nothing.
    assert stored == [
        ("US_EQ_AAPL", "abnormal_return"),
        ("US_EQ_AAPL", "abnormal_volume"),
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py::test_run_event_scan_emits_and_persists_events -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.events.scan'`

- [ ] **Step 3: Implement the scan job**

Create `croesus/events/scan.py`:

```python
from __future__ import annotations

from datetime import date
from typing import Callable

import duckdb

from croesus.assets.repository import AssetRepository
from croesus.disclosures.repository import DisclosureRepository
from croesus.events.detectors import detect_events
from croesus.events.models import EventScanResult
from croesus.events.repository import EventRepository
from croesus.factors.equity.repository import ValuationSnapshotRepository
from croesus.prices.repository import PriceRepository

# Operating companies are the event subjects (matches the disclosure funnel).
SCAN_ASSET_TYPES = ("equity",)


def run_event_scan(
    conn: duckdb.DuckDBPyConnection,
    *,
    as_of_date: date | None = None,
    log: Callable[[str], None] = print,
) -> EventScanResult:
    """Run the deterministic detectors over every active equity and persist events.

    ``as_of_date`` defaults to the latest price date in the DB. Per-asset failures
    are isolated so one bad series never stops the scan.
    """
    if as_of_date is None:
        as_of_date = _latest_price_date(conn) or date.today()

    assets = [
        a
        for a in AssetRepository(conn).list_active()
        if a.asset_type in SCAN_ASSET_TYPES
    ]
    prices_repo = PriceRepository(conn)
    valuation_repo = ValuationSnapshotRepository(conn)
    disclosure_repo = DisclosureRepository(conn)
    event_repo = EventRepository(conn)
    result = EventScanResult()

    for asset in assets:
        try:
            # Forward-only scan: detectors evaluate the latest available row.
            # ``as_of_date`` defaults to that row's date, so no point-in-time
            # slice is needed here (B2 is not a backtest — spec §정직한 검증 한계).
            # The disclosure/valuation detectors still apply their own ``<= as_of``
            # filtering (filed_date window / SQL date<=as_of) for correctness.
            prices = prices_repo.load_daily_prices(asset.asset_id)
            snapshot = valuation_repo.get(asset.asset_id, as_of_date)
            disclosures = disclosure_repo.load_for_asset(asset.asset_id)
            events = detect_events(
                asset.asset_id, as_of_date, prices, snapshot, disclosures
            )
            event_repo.upsert(events)
            result.scanned.append(asset.symbol)
            result.events.extend(events)
            if events:
                log(f"{asset.symbol}: {len(events)} event(s)")
        except Exception as exc:  # noqa: BLE001 - per-asset failures must not stop the scan.
            result.failed[asset.symbol] = str(exc)
            log(f"failed {asset.symbol}: {exc}")

    return result


def _latest_price_date(conn: duckdb.DuckDBPyConnection) -> date | None:
    row = conn.execute("SELECT MAX(date) FROM prices_daily").fetchone()
    return row[0] if row and row[0] is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_events.py::test_run_event_scan_emits_and_persists_events -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/events/scan.py tests/test_events.py
git commit -m "✨ feat: add run_event_scan orchestration job"
```

---

### Task 7: Wire into `local_sync` and freshness tracking

**Files:**
- Modify: `croesus/jobs/run_status.py` (add a `DomainSpec` to `DOMAIN_REGISTRY`, after the `disclosures` entry)
- Modify: `croesus/jobs/local_sync.py` (add `_run_event_scan` runner near the other `_run_*` functions; register a `SyncJob` in `default_sync_jobs()` immediately after `daily_run`)
- Modify: `tests/test_local_sync.py` (add `"event_scan"` to the exact-ordered job-name list in `test_default_jobs_are_recommendation_only_no_trades`, right after `"daily_run"`)
- Test: `tests/test_events.py` (add)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_events.py`:

```python
def test_event_scan_registered_in_sync_pipeline() -> None:
    from croesus.jobs.local_sync import default_sync_jobs
    from croesus.jobs.run_status import DOMAINS_BY_NAME

    assert "events" in DOMAINS_BY_NAME
    assert DOMAINS_BY_NAME["events"].job_name == "event_scan"

    jobs = {job.name: job for job in default_sync_jobs()}
    assert "event_scan" in jobs
    scan_job = jobs["event_scan"]
    assert scan_job.domains == ("events",)
    # Needs fresh prices + valuation (daily_run); reacts to new disclosures softly.
    assert scan_job.depends_on == ("daily_run",)
    assert scan_job.soft_depends_on == ("disclosures_run",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_events.py::test_event_scan_registered_in_sync_pipeline -v`
Expected: FAIL — `"events" not in DOMAINS_BY_NAME`.

- [ ] **Step 3: Add the freshness domain**

In `croesus/jobs/run_status.py`, inside the `DOMAIN_REGISTRY` tuple, add this entry immediately after the `disclosures` `DomainSpec`:

```python
    # The event scan writes nothing on a genuinely quiet day, so MAX(as_of_date)
    # would read stale even after a clean run. Like asset_universe/disclosures,
    # key freshness to the job's own last success.
    DomainSpec(
        "events", "event_scan", 48.0,
        lambda c: _scalar_date(
            c,
            "SELECT MAX(finished_at) FROM job_runs "
            "WHERE job_name = 'event_scan' AND status = 'success'",
        ),
    ),
```

- [ ] **Step 4: Add the runner**

In `croesus/jobs/local_sync.py`, add this function next to the other `_run_*` runners (e.g. after `_run_daily`):

```python
def _run_event_scan(db: Path) -> str:
    from croesus.events.scan import run_event_scan

    with get_connection(db) as conn:
        result = run_event_scan(conn)
    return (
        f"event_scan scanned={len(result.scanned)} "
        f"events={len(result.events)} fail={len(result.failed)}"
    )
```

- [ ] **Step 5: Register the job**

In `croesus/jobs/local_sync.py`, in `default_sync_jobs()`, add this `SyncJob` to the returned list immediately after the `daily_run` entry (so its `depends_on=("daily_run",)` resolves in order):

```python
        SyncJob(
            "event_scan", ("events",), _run_event_scan,
            depends_on=("daily_run",),
            soft_depends_on=("disclosures_run",),
        ),
```

- [ ] **Step 6: Update the exact-order sync test**

In `tests/test_local_sync.py`, in `test_default_jobs_are_recommendation_only_no_trades`, add `"event_scan"` to the expected ordered job-name list immediately after `"daily_run"`.

- [ ] **Step 7: Run the tests**

Run: `pytest tests/test_events.py::test_event_scan_registered_in_sync_pipeline tests/test_local_sync.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add croesus/jobs/run_status.py croesus/jobs/local_sync.py tests/test_events.py tests/test_local_sync.py
git commit -m "✨ feat: wire event scan into local_sync pipeline"
```

---

### Task 8: Full regression

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `pytest -q`
Expected: PASS — all pre-existing tests (494 after B1) plus the new event tests are green.

- [ ] **Step 2: Confirm clean tree**

Run: `git status --short`
Expected: clean (everything committed).

---

## Self-Review (controller checklist — done while writing this plan)

**1. Spec coverage (spec §"후보 소싱" + Phase B "싼 이벤트 전처리"):**
- "이벤트·이상 트리거: 신규 8-K/공시, 가이던스 변경, 뉴스 급증, 이상 거래량·수익률, 밸류 디스로케이션" →
  - 신규 8-K/공시 → `detect_recent_disclosure` (Task 4) ✅
  - 이상 거래량 → `detect_abnormal_volume` (Task 3) ✅
  - 이상 수익률 → `detect_abnormal_return` (Task 3) ✅
  - 밸류 디스로케이션 → `detect_valuation_dislocation` (Task 4) ✅
  - 가이던스 변경 / 뉴스 급증 → **deferred** (need 8-K *text* / a news API — neither ingested yet). Documented in Scope; schema accepts new `event_type`s without migration. ✅ (explicit deferral, not a silent gap)
- "싼 결정론적 전처리 (LLM 없음)" → all detectors are pure threshold math; no LLM anywhere. ✅
- "후보군 (팩터 스크린이 안 잡는 종목 포함)" → scans the whole active-equity universe, not a factor shortlist. ✅
- Guardrails (recommendation-only, no auto trade) → `events` rows are inert candidates; nothing sizes/executes. ✅

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". Every code step is complete. ✅

**3. Type consistency:** `Event` fields are identical across Tasks 2–7; `detect_*` signatures match their call sites in `detect_events` (Task 4) and `run_event_scan` (Task 6); `EventScanResult` fields (`scanned`/`events`/`failed`) match the Task 6 assertions; `EventRepository.upsert/load_for_date` signatures match Task 5/6 usage; `ValuationSnapshot`/`Disclosure` imports use the real existing classes (`croesus.factors.equity.repository.ValuationSnapshot`, `croesus.disclosures.models.Disclosure`); `DomainSpec("events","event_scan",…)` job_name matches `SyncJob("event_scan",…)` (Task 7). ✅

**4. Reuse check:** detectors compute from the price DataFrame directly (same idiom as `croesus/factors/common.py`) rather than re-reading `factor_values`; valuation dislocation reads the existing `upside_pct` instead of recomputing a DCF; freshness reuses the `asset_universe`/`disclosures` job-success pattern. No duplicated infrastructure. ✅
