# Phase E — Opportunity Risk-Gate Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach a recommendation-only risk-gate verdict (pass/warn/block) to each user-selected opportunity candidate, checking portfolio concentration, asset-type eligibility, and a liquidity floor — surfaced in the existing opportunity review report.

**Architecture:** A new pure decision function `evaluate_risk_gate` plus a DB orchestrator `evaluate_candidates` in `croesus/opportunities/risk_gate.py`. The orchestrator reuses `compute_exposures` (fresh, from current holdings + profile caps) and a shared `load_asset_attrs` helper extracted from `rebalance_check`. `run_opportunity_review` attaches verdicts to `OpportunityCard`; the report and CLI render them. No new tables, no persistence of verdicts.

**Tech Stack:** Python 3, DuckDB, pytest. Dataclasses (frozen). No new dependencies.

## Global Constraints

- Recommendation-only: the gate never proposes trades, never re-ranks cards, never writes to the portfolio layer. `OpportunityReviewResult.recommendation_only` stays `True`.
- No position-sizing simulation: capacity is judged from current bucket `is_violation` flags only (block_new_buy semantics), never from an assumed weight.
- Liquidity below the floor is **WARN**, never BLOCK (user decision). `min_liquidity_usd = 0` disables the check.
- Reuse existing reason-code strings verbatim: `SECTOR_OVER_MAX`, `INDUSTRY_OVER_MAX`, `COUNTRY_OVER_MAX`, `CURRENCY_OVER_MAX`, `POSITION_OVER_MAX`, `LIQUIDITY_BELOW_MINIMUM`. New code: `DISALLOWED_ASSET_TYPE`, `ALREADY_HELD` (note only).
- Backward compatibility: `OpportunityCard.risk_gate` defaults to `None`; with `apply_risk_gate=False` or an unloadable profile, output matches prior behavior.
- `DEFAULT_MIN_LIQUIDITY_USD = 1_000_000`.
- Status precedence: `block` > `warn` > `pass`.
- Commit messages use gitmoji per repo CLAUDE.md, ending with the Co-Authored-By trailer.
- Run tests with: `cd <repo root> && python -m pytest <path> -v` (the worktree root).

## File Structure

- Create: `croesus/opportunities/risk_gate.py` — verdict dataclass, `DEFAULT_MIN_LIQUIDITY_USD`, `evaluate_risk_gate` (pure), `evaluate_candidates` (orchestrator).
- Create: `croesus/portfolio/asset_attrs.py` — `load_asset_attrs(conn, asset_ids)` shared helper.
- Modify: `croesus/jobs/rebalance_check.py` — import shared `load_asset_attrs`, drop the private copy.
- Modify: `croesus/opportunities/review.py` — `OpportunityCard.risk_gate` field; `run_opportunity_review` attaches verdicts.
- Modify: `croesus/reports/opportunity.py` — render the gate column + per-card detail; gate summary header.
- Modify: `croesus/jobs/opportunity_review.py` — `--portfolio-id`, `--profile-id`, `--no-risk-gate`, `--min-liquidity-usd` args.
- Test files: `tests/opportunities/test_risk_gate.py`, `tests/opportunities/test_review_risk_gate.py`, `tests/reports/test_opportunity_risk_gate_report.py`, `tests/portfolio/test_asset_attrs.py`.

> Confirm the test directory layout before Task 1 (run `ls tests/opportunities tests/portfolio tests/reports`). If a subdir does not exist, create it with an empty `__init__.py` only if sibling test dirs use them — match the existing convention.

---

### Task 1: Extract shared `load_asset_attrs` helper

**Files:**
- Create: `croesus/portfolio/asset_attrs.py`
- Modify: `croesus/jobs/rebalance_check.py` (remove private `_load_asset_attrs`, lines 180-208; import shared one; the `json` import may become unused — remove it only if no other use remains in the file)
- Test: `tests/portfolio/test_asset_attrs.py`

**Interfaces:**
- Produces: `load_asset_attrs(conn: duckdb.DuckDBPyConnection, asset_ids: list[str]) -> dict[str, AssetAttrs]` — identical behavior to the current private `rebalance_check._load_asset_attrs`: dedups + sorts ids, skips `CASH_*`, reads `asset_id, asset_type, sector, industry, country, currency, name, metadata` from `assets`, parses JSON `metadata.theme_tags` into `AssetAttrs.theme_tags`.

- [ ] **Step 1: Write the failing test**

```python
# tests/portfolio/test_asset_attrs.py
from croesus.db.migrate import migrate
from croesus.db.connection import get_connection
from croesus.portfolio.asset_attrs import load_asset_attrs


def _seed_asset(conn, asset_id, **kw):
    conn.execute(
        """INSERT INTO assets
           (asset_id, symbol, name, asset_type, country, exchange, currency,
            sector, industry, is_active, source, metadata)
           VALUES (?, ?, ?, ?, ?, 'NMS', ?, ?, ?, true, 'test', ?)""",
        [
            asset_id, kw.get("symbol", asset_id), kw.get("name", asset_id),
            kw.get("asset_type", "equity"), kw.get("country", "US"),
            kw.get("currency", "USD"), kw.get("sector", "Technology"),
            kw.get("industry", "Software"), kw.get("metadata", '{"theme_tags": ["ai"]}'),
        ],
    )


def test_load_asset_attrs_parses_theme_tags_and_skips_cash(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        _seed_asset(conn, "EQ1", sector="Technology", industry="Software")
        attrs = load_asset_attrs(conn, ["EQ1", "CASH_USD", "EQ1"])
    assert set(attrs) == {"EQ1"}
    assert attrs["EQ1"].sector == "Technology"
    assert attrs["EQ1"].asset_type == "equity"
    assert attrs["EQ1"].theme_tags == ["ai"]


def test_load_asset_attrs_empty_returns_empty(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        assert load_asset_attrs(conn, []) == {}
        assert load_asset_attrs(conn, ["CASH_USD"]) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/portfolio/test_asset_attrs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.portfolio.asset_attrs'`

- [ ] **Step 3: Create the shared helper**

```python
# croesus/portfolio/asset_attrs.py
from __future__ import annotations

import json

import duckdb

from croesus.portfolio.models import AssetAttrs


def load_asset_attrs(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, AssetAttrs]:
    """Build classification attributes for assets from the ``assets`` table.

    Dedups and sorts ids for a stable query, skips synthetic ``CASH_*`` ids
    (the caller supplies cash attrs separately), and parses ``metadata.theme_tags``.
    """
    lookup = [a for a in sorted(set(asset_ids)) if not a.startswith("CASH_")]
    if not lookup:
        return {}
    placeholders = ", ".join("?" for _ in lookup)
    rows = conn.execute(
        f"""
        SELECT asset_id, asset_type, sector, industry, country, currency, name, metadata
        FROM assets
        WHERE asset_id IN ({placeholders})
        """,
        lookup,
    ).fetchall()
    attrs: dict[str, AssetAttrs] = {}
    for asset_id, asset_type, sector, industry, country, currency, name, metadata in rows:
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        attrs[asset_id] = AssetAttrs(
            asset_type=asset_type,
            sector=sector,
            industry=industry,
            country=country,
            currency=currency,
            theme_tags=list((metadata or {}).get("theme_tags") or []),
            name=name,
        )
    return attrs
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/portfolio/test_asset_attrs.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Point rebalance_check at the shared helper**

In `croesus/jobs/rebalance_check.py`: delete the private `_load_asset_attrs` function (lines ~180-208), add `from croesus.portfolio.asset_attrs import load_asset_attrs` near the other `croesus.portfolio` imports, and change the call site `assets_by_id = _load_asset_attrs(conn, asset_ids)` to `assets_by_id = load_asset_attrs(conn, asset_ids)`. If `import json` is now unused in the file, remove it.

- [ ] **Step 6: Run the rebalance tests to verify no regression**

Run: `python -m pytest tests/jobs/test_rebalance_check.py -v` (adjust path if the rebalance test file lives elsewhere — find via `ls tests/jobs | grep rebalance`)
Expected: PASS (unchanged behavior)

- [ ] **Step 7: Commit**

```bash
git add croesus/portfolio/asset_attrs.py croesus/jobs/rebalance_check.py tests/portfolio/test_asset_attrs.py
git commit -m "$(cat <<'EOF'
♻️ refactor: extract shared load_asset_attrs helper

Phase E reuses the assets-table classification loader; lift it out of
rebalance_check into croesus/portfolio/asset_attrs.py. No behavior change.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `evaluate_risk_gate` pure decision function

**Files:**
- Create: `croesus/opportunities/risk_gate.py`
- Test: `tests/opportunities/test_risk_gate.py`

**Interfaces:**
- Consumes: `Exposure`, `AssetAttrs` from `croesus.portfolio.models`; `InvestorProfile`, `AssetType` from `croesus.profiles.models`.
- Produces:
  - `DEFAULT_MIN_LIQUIDITY_USD = 1_000_000`
  - `@dataclass(frozen=True) class RiskGateVerdict: status: str; reason_codes: list[str]; notes: list[str]`
  - `def evaluate_risk_gate(candidate_asset_id: str, candidate: AssetAttrs, *, exposures: list[Exposure], held_asset_ids: set[str], profile: InvestorProfile, liquidity_value: float | None, min_liquidity_usd: float | None) -> RiskGateVerdict`

**Decision logic (priority block > warn > pass):**
- For each `Exposure` with `is_violation=True`: if its `exposure_type`/`exposure_name` matches the candidate's bucket → block reason. Mapping: `sector`→`SECTOR_OVER_MAX` (match `exposure_name == candidate.sector`), `industry`→`INDUSTRY_OVER_MAX`, `country`→`COUNTRY_OVER_MAX`, `currency`→`CURRENCY_OVER_MAX`, `position`→`POSITION_OVER_MAX` (match `exposure_name == candidate_asset_id`, i.e. already-held over-cap).
- Asset-type: build `disallowed = {t.value for t in profile.disallowed_asset_types}` and `allowed = {t.value for t in profile.allowed_asset_types}`. If `candidate.asset_type in disallowed`, or (`allowed` non-empty and `candidate.asset_type not in allowed`) → block `DISALLOWED_ASSET_TYPE`.
- Liquidity: if `min_liquidity_usd` truthy (`> 0`) and (`liquidity_value is None` or `liquidity_value < min_liquidity_usd`) → warn `LIQUIDITY_BELOW_MINIMUM`.
- If `candidate_asset_id in held_asset_ids` → append note `ALREADY_HELD` (does not change status).

- [ ] **Step 1: Write the failing tests**

```python
# tests/opportunities/test_risk_gate.py
from datetime import date

from croesus.opportunities.risk_gate import (
    DEFAULT_MIN_LIQUIDITY_USD,
    RiskGateVerdict,
    evaluate_risk_gate,
)
from croesus.portfolio.models import AssetAttrs, Exposure
from croesus.profiles.models import AssetType, Currency, InvestorProfile, TradeMode


def _profile(*, allowed=None, disallowed=None) -> InvestorProfile:
    return InvestorProfile(
        profile_id="default", name="Default", base_currency=Currency.USD,
        expected_annual_return=0.08, max_tolerable_drawdown=-0.30,
        investment_horizon_years=10, monthly_contribution=0.0,
        liquidity_buffer_months=6.0,
        allowed_asset_types=allowed or [], disallowed_asset_types=disallowed or [],
        max_single_position_weight=0.10, max_sector_weight=0.25,
        max_industry_weight=0.20, max_theme_weight=0.30,
        max_country_weight=0.80, max_currency_weight=0.80,
        max_monthly_turnover=0.20, rebalance_band=0.05,
        trade_mode=TradeMode.MANUAL, metadata={},
    )


def _exposure(exposure_type, name, weight, cap, is_violation) -> Exposure:
    return Exposure(
        portfolio_id="default", as_of_date=date(2026, 6, 23),
        exposure_type=exposure_type, exposure_name=name, weight=weight,
        market_value=weight * 1000, limit_weight=cap, is_violation=is_violation,
    )


def _attrs(**kw) -> AssetAttrs:
    return AssetAttrs(
        asset_type=kw.get("asset_type", "equity"),
        sector=kw.get("sector", "Healthcare"),
        industry=kw.get("industry", "Pharma"),
        country=kw.get("country", "US"),
        currency=kw.get("currency", "USD"),
    )


def test_clean_candidate_passes():
    v = evaluate_risk_gate(
        "LLY", _attrs(sector="Healthcare"),
        exposures=[_exposure("sector", "Technology", 0.30, 0.25, True)],
        held_asset_ids=set(), profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "pass"
    assert v.reason_codes == []


def test_sector_over_cap_blocks():
    v = evaluate_risk_gate(
        "NVDA", _attrs(sector="Technology"),
        exposures=[_exposure("sector", "Technology", 0.30, 0.25, True)],
        held_asset_ids=set(), profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "SECTOR_OVER_MAX" in v.reason_codes


def test_already_held_position_over_cap_blocks():
    v = evaluate_risk_gate(
        "AAPL", _attrs(sector="Technology"),
        exposures=[_exposure("position", "AAPL", 0.12, 0.10, True)],
        held_asset_ids={"AAPL"}, profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "POSITION_OVER_MAX" in v.reason_codes
    assert any("ALREADY_HELD" in n for n in v.notes)


def test_disallowed_asset_type_blocks():
    v = evaluate_risk_gate(
        "X", _attrs(asset_type="crypto"),
        exposures=[], held_asset_ids=set(),
        profile=_profile(disallowed=[AssetType.CRYPTO]),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "DISALLOWED_ASSET_TYPE" in v.reason_codes


def test_asset_type_not_in_allowlist_blocks():
    v = evaluate_risk_gate(
        "X", _attrs(asset_type="reit"),
        exposures=[], held_asset_ids=set(),
        profile=_profile(allowed=[AssetType.EQUITY, AssetType.ETF]),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "DISALLOWED_ASSET_TYPE" in v.reason_codes


def test_low_liquidity_warns():
    v = evaluate_risk_gate(
        "TINY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=100_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "warn"
    assert "LIQUIDITY_BELOW_MINIMUM" in v.reason_codes


def test_missing_liquidity_warns():
    v = evaluate_risk_gate(
        "TINY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=None, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "warn"
    assert "LIQUIDITY_BELOW_MINIMUM" in v.reason_codes


def test_liquidity_check_disabled_when_floor_zero():
    v = evaluate_risk_gate(
        "TINY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=None, min_liquidity_usd=0,
    )
    assert v.status == "pass"


def test_empty_portfolio_passes_eligibility_only():
    v = evaluate_risk_gate(
        "LLY", _attrs(),
        exposures=[], held_asset_ids=set(), profile=_profile(),
        liquidity_value=5_000_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "pass"


def test_block_precedence_over_warn():
    v = evaluate_risk_gate(
        "NVDA", _attrs(sector="Technology"),
        exposures=[_exposure("sector", "Technology", 0.30, 0.25, True)],
        held_asset_ids=set(), profile=_profile(),
        liquidity_value=100_000, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD,
    )
    assert v.status == "block"
    assert "SECTOR_OVER_MAX" in v.reason_codes
    assert "LIQUIDITY_BELOW_MINIMUM" in v.reason_codes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/opportunities/test_risk_gate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'croesus.opportunities.risk_gate'`

- [ ] **Step 3: Implement the pure function**

```python
# croesus/opportunities/risk_gate.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import duckdb

from croesus.portfolio.asset_attrs import load_asset_attrs
from croesus.portfolio.exposure import ExposureLimits, compute_exposures
from croesus.portfolio.models import AssetAttrs, Exposure
from croesus.portfolio.repository import PortfolioRepository
from croesus.profiles.models import InvestorProfile
from croesus.profiles.repository import ProfileRepository

DEFAULT_MIN_LIQUIDITY_USD = 1_000_000

_BUCKET_REASON = {
    "sector": "SECTOR_OVER_MAX",
    "industry": "INDUSTRY_OVER_MAX",
    "country": "COUNTRY_OVER_MAX",
    "currency": "CURRENCY_OVER_MAX",
}


@dataclass(frozen=True)
class RiskGateVerdict:
    status: str  # 'pass' | 'warn' | 'block'
    reason_codes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _candidate_bucket_name(candidate: AssetAttrs, exposure_type: str) -> str | None:
    if exposure_type == "sector":
        return candidate.sector or "Unknown"
    if exposure_type == "industry":
        return candidate.industry or "Unknown"
    if exposure_type == "country":
        return candidate.country or "Unknown"
    if exposure_type == "currency":
        return candidate.currency or "Unknown"
    return None


def evaluate_risk_gate(
    candidate_asset_id: str,
    candidate: AssetAttrs,
    *,
    exposures: list[Exposure],
    held_asset_ids: set[str],
    profile: InvestorProfile,
    liquidity_value: float | None,
    min_liquidity_usd: float | None,
) -> RiskGateVerdict:
    """Decide pass/warn/block for one prospective-buy candidate. Pure."""
    block_codes: list[str] = []
    warn_codes: list[str] = []
    notes: list[str] = []

    for exp in exposures:
        if not exp.is_violation:
            continue
        if exp.exposure_type == "position":
            if exp.exposure_name == candidate_asset_id:
                block_codes.append("POSITION_OVER_MAX")
                notes.append(
                    f"POSITION_OVER_MAX: {candidate_asset_id} weight "
                    f"{exp.weight:.1%} > cap {exp.limit_weight:.1%}"
                )
            continue
        reason = _BUCKET_REASON.get(exp.exposure_type)
        if reason is None:
            continue
        if exp.exposure_name == _candidate_bucket_name(candidate, exp.exposure_type):
            block_codes.append(reason)
            notes.append(
                f"{reason}: {exp.exposure_type} '{exp.exposure_name}' "
                f"{exp.weight:.1%} > cap {exp.limit_weight:.1%} (no room for new buy)"
            )

    disallowed = {t.value for t in profile.disallowed_asset_types}
    allowed = {t.value for t in profile.allowed_asset_types}
    atype = candidate.asset_type
    if atype is not None and (
        atype in disallowed or (allowed and atype not in allowed)
    ):
        block_codes.append("DISALLOWED_ASSET_TYPE")
        notes.append(f"DISALLOWED_ASSET_TYPE: asset_type '{atype}' not permitted by profile")

    if min_liquidity_usd and (
        liquidity_value is None or liquidity_value < min_liquidity_usd
    ):
        warn_codes.append("LIQUIDITY_BELOW_MINIMUM")
        shown = "n/a" if liquidity_value is None else f"${liquidity_value:,.0f}"
        notes.append(
            f"LIQUIDITY_BELOW_MINIMUM: liquidity_1m {shown} < floor ${min_liquidity_usd:,.0f}"
        )

    if candidate_asset_id in held_asset_ids:
        notes.append(f"ALREADY_HELD: {candidate_asset_id} is in the current portfolio")

    if block_codes:
        status = "block"
    elif warn_codes:
        status = "warn"
    else:
        status = "pass"
    return RiskGateVerdict(status=status, reason_codes=[*block_codes, *warn_codes], notes=notes)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/opportunities/test_risk_gate.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add croesus/opportunities/risk_gate.py tests/opportunities/test_risk_gate.py
git commit -m "$(cat <<'EOF'
✨ feat: add opportunity risk-gate decision function

Pure pass/warn/block verdict over a prospective-buy candidate: bucket
capacity (block_new_buy), asset-type eligibility, liquidity floor (warn).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `evaluate_candidates` DB orchestrator

**Files:**
- Modify: `croesus/opportunities/risk_gate.py` (append orchestrator + a liquidity loader)
- Test: `tests/opportunities/test_risk_gate.py` (append orchestrator tests, or a new `test_risk_gate_orchestrator.py`)

**Interfaces:**
- Consumes: `ProfileRepository.get_profile(profile_id) -> InvestorProfile | None`; `PortfolioRepository.get_holdings(portfolio_id, as_of_date) -> list[Holding]`; `load_asset_attrs`; `compute_exposures`; `ExposureLimits`.
- Produces: `def evaluate_candidates(conn, candidate_asset_ids: Sequence[str], *, portfolio_id: str, profile_id: str, as_of_date: date, min_liquidity_usd: float | None = DEFAULT_MIN_LIQUIDITY_USD) -> dict[str, RiskGateVerdict]` — returns `{}` when the profile cannot be loaded (caller leaves cards ungated).

**Behavior:**
1. `profile = ProfileRepository(conn).get_profile(profile_id)`; if `None` → return `{}`.
2. Resolve the snapshot date: if `as_of_date` given use it, else latest `portfolio_snapshots.as_of_date` for the portfolio (may be `None` → no holdings).
3. `holdings = PortfolioRepository(conn).get_holdings(portfolio_id, snapshot_date)` (empty list if no snapshot).
4. `held = {h.asset_id for h in holdings}`.
5. `attrs = load_asset_attrs(conn, [h.asset_id for h in holdings] + list(candidate_asset_ids))`.
6. `limits = ExposureLimits(max_single_position_weight=profile.max_single_position_weight, max_sector_weight=profile.max_sector_weight, max_industry_weight=profile.max_industry_weight, max_theme_weight=profile.max_theme_weight, max_country_weight=profile.max_country_weight, max_currency_weight=profile.max_currency_weight)`.
7. `exposures = compute_exposures(holdings, attrs, limits, portfolio_id=portfolio_id, as_of_date=snapshot_date or as_of_date or date.today())`.
8. `liquidity = _load_liquidity(conn, candidate_asset_ids, as_of_date)` — latest `liquidity_1m` per candidate at or before `as_of_date`.
9. For each candidate id: `evaluate_risk_gate(id, attrs.get(id, AssetAttrs()), exposures=exposures, held_asset_ids=held, profile=profile, liquidity_value=liquidity.get(id), min_liquidity_usd=min_liquidity_usd)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/opportunities/test_risk_gate.py
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.opportunities.risk_gate import evaluate_candidates
from croesus.profiles.repository import ProfileRepository


def _seed_asset(conn, asset_id, **kw):
    conn.execute(
        """INSERT INTO assets
           (asset_id, symbol, name, asset_type, country, exchange, currency,
            sector, industry, is_active, source, metadata)
           VALUES (?, ?, ?, ?, ?, 'NMS', ?, ?, ?, true, 'test', '{}')""",
        [asset_id, kw.get("symbol", asset_id), kw.get("name", asset_id),
         kw.get("asset_type", "equity"), kw.get("country", "US"),
         kw.get("currency", "USD"), kw.get("sector", "Technology"),
         kw.get("industry", "Software")],
    )


def _seed_profile(conn):
    # ProfileRepository persistence helper — confirm the actual write API
    # (e.g. ProfileRepository(conn).upsert_profile / save_profile) before use.
    from croesus.profiles.seed_default_profile import seed_default_profile
    seed_default_profile(conn)


def test_evaluate_candidates_missing_profile_returns_empty(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        out = evaluate_candidates(
            conn, ["EQ1"], portfolio_id="default",
            profile_id="nope", as_of_date=date(2026, 6, 23),
        )
    assert out == {}


def test_evaluate_candidates_empty_portfolio_eligibility_only(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        _seed_profile(conn)
        _seed_asset(conn, "EQ1", sector="Technology")
        conn.execute(
            "INSERT INTO factor_values VALUES ('EQ1', ?, 'liquidity_1m', 5000000)",
            [date(2026, 6, 23)],
        )
        out = evaluate_candidates(
            conn, ["EQ1"], portfolio_id="default",
            profile_id="default", as_of_date=date(2026, 6, 23),
        )
    assert out["EQ1"].status == "pass"


def test_evaluate_candidates_low_liquidity_warns(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        _seed_profile(conn)
        _seed_asset(conn, "EQ1", sector="Technology")
        conn.execute(
            "INSERT INTO factor_values VALUES ('EQ1', ?, 'liquidity_1m', 50000)",
            [date(2026, 6, 23)],
        )
        out = evaluate_candidates(
            conn, ["EQ1"], portfolio_id="default",
            profile_id="default", as_of_date=date(2026, 6, 23),
        )
    assert out["EQ1"].status == "warn"
    assert "LIQUIDITY_BELOW_MINIMUM" in out["EQ1"].reason_codes
```

> Before running: confirm the `factor_values` column order with `python -c "from croesus.db.connection import get_connection; from croesus.db.migrate import migrate; migrate('x.duckdb'); print(get_connection('x.duckdb').execute('DESCRIBE factor_values').fetchall())"` and adjust the positional INSERTs above to match (the schema is `(asset_id, date, factor_name, value)`). Likewise confirm the profile-seeding API name in `croesus/profiles/seed_default_profile.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/opportunities/test_risk_gate.py -k evaluate_candidates -v`
Expected: FAIL with `ImportError: cannot import name 'evaluate_candidates'`

- [ ] **Step 3: Implement the orchestrator**

```python
# append to croesus/opportunities/risk_gate.py

def _latest_snapshot_date(
    conn: duckdb.DuckDBPyConnection, portfolio_id: str
) -> date | None:
    row = conn.execute(
        """
        SELECT as_of_date FROM portfolio_snapshots
        WHERE portfolio_id = ? ORDER BY as_of_date DESC LIMIT 1
        """,
        [portfolio_id],
    ).fetchone()
    return row[0] if row else None


def _load_liquidity(
    conn: duckdb.DuckDBPyConnection,
    asset_ids: Sequence[str],
    as_of_date: date,
) -> dict[str, float]:
    ids = list(dict.fromkeys(asset_ids))
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT asset_id, value,
                   ROW_NUMBER() OVER (PARTITION BY asset_id ORDER BY date DESC) AS rn
            FROM factor_values
            WHERE factor_name = 'liquidity_1m'
              AND date <= ?
              AND asset_id IN ({placeholders})
        )
        SELECT asset_id, value FROM ranked WHERE rn = 1
        """,
        [as_of_date, *ids],
    ).fetchall()
    return {asset_id: value for asset_id, value in rows}


def evaluate_candidates(
    conn: duckdb.DuckDBPyConnection,
    candidate_asset_ids: Sequence[str],
    *,
    portfolio_id: str,
    profile_id: str,
    as_of_date: date,
    min_liquidity_usd: float | None = DEFAULT_MIN_LIQUIDITY_USD,
) -> dict[str, RiskGateVerdict]:
    """Gather portfolio/profile/liquidity inputs and verdict each candidate.

    Returns ``{}`` when the profile is missing so the caller can leave cards
    ungated. An absent snapshot yields no holdings -> eligibility-only verdicts.
    """
    profile = ProfileRepository(conn).get_profile(profile_id)
    if profile is None:
        return {}

    snapshot_date = as_of_date or _latest_snapshot_date(conn, portfolio_id)
    portfolio_repo = PortfolioRepository(conn)
    holdings = (
        portfolio_repo.get_holdings(portfolio_id, snapshot_date)
        if snapshot_date is not None
        else []
    )
    held = {h.asset_id for h in holdings}
    attrs = load_asset_attrs(
        conn, [h.asset_id for h in holdings] + list(candidate_asset_ids)
    )
    limits = ExposureLimits(
        max_single_position_weight=profile.max_single_position_weight,
        max_sector_weight=profile.max_sector_weight,
        max_industry_weight=profile.max_industry_weight,
        max_theme_weight=profile.max_theme_weight,
        max_country_weight=profile.max_country_weight,
        max_currency_weight=profile.max_currency_weight,
    )
    exposures = compute_exposures(
        holdings, attrs, limits,
        portfolio_id=portfolio_id,
        as_of_date=snapshot_date or as_of_date,
    )
    liquidity = _load_liquidity(conn, candidate_asset_ids, as_of_date)

    return {
        asset_id: evaluate_risk_gate(
            asset_id,
            attrs.get(asset_id, AssetAttrs()),
            exposures=exposures,
            held_asset_ids=held,
            profile=profile,
            liquidity_value=liquidity.get(asset_id),
            min_liquidity_usd=min_liquidity_usd,
        )
        for asset_id in candidate_asset_ids
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/opportunities/test_risk_gate.py -v`
Expected: PASS (all tests including orchestrator)

- [ ] **Step 5: Commit**

```bash
git add croesus/opportunities/risk_gate.py tests/opportunities/test_risk_gate.py
git commit -m "$(cat <<'EOF'
✨ feat: add risk-gate candidate orchestrator

evaluate_candidates loads profile + holdings + liquidity, computes fresh
exposures, and verdicts each candidate. Missing profile -> empty (ungated);
absent snapshot -> eligibility-only.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Attach verdicts in `run_opportunity_review`

**Files:**
- Modify: `croesus/opportunities/review.py`
- Test: `tests/opportunities/test_review_risk_gate.py`

**Interfaces:**
- Consumes: `evaluate_candidates` from Task 3; `RiskGateVerdict`.
- Produces:
  - `OpportunityCard` gains trailing field `risk_gate: RiskGateVerdict | None = None`.
  - `run_opportunity_review(conn, *, methodology_key=None, methodology=None, as_of_date=None, limit=20, portfolio_id="default", profile_id="default", apply_risk_gate=True, min_liquidity_usd=DEFAULT_MIN_LIQUIDITY_USD) -> OpportunityReviewResult`.
  - `OpportunityReviewResult` gains trailing field `gate_summary: dict[str, int] | None = None` (counts keyed `pass`/`warn`/`block`; `None` when the gate did not run).

**Behavior:** after `cards` are built and sorted, if `apply_risk_gate`: call `evaluate_candidates` for `[c.asset_id for c in cards]`; if it returns a non-empty dict, rebuild each card via `dataclasses.replace(card, risk_gate=verdicts.get(card.asset_id))` and compute `gate_summary` counts; if it returns `{}` (no profile), leave cards ungated and `gate_summary=None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/opportunities/test_review_risk_gate.py
from datetime import date

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.opportunities.review import run_opportunity_review
from croesus.profiles.seed_default_profile import seed_default_profile


def _seed_band_asset(conn, asset_id, sector, base_iv, price, liquidity):
    conn.execute(
        """INSERT INTO assets
           (asset_id, symbol, name, asset_type, country, exchange, currency,
            sector, industry, is_active, source, metadata)
           VALUES (?, ?, ?, 'equity', 'US', 'NMS', 'USD', ?, 'Sub', true, 'test', '{}')""",
        [asset_id, asset_id, asset_id, sector],
    )
    d = date(2026, 6, 23)
    for scenario, iv in (("bear", base_iv * 0.7), ("base", base_iv), ("bull", base_iv * 1.3)):
        conn.execute(
            """INSERT INTO intrinsic_value_bands
               (asset_id, date, scenario, intrinsic_value_per_share, current_price,
                upside_pct, wacc, fcf_growth_rate, terminal_growth_rate,
                explicit_years, wacc_risk_premium, moat_grade, sector_grade,
                disruption_grade, thesis_as_of_date, thesis_run_id)
               VALUES (?, ?, ?, ?, ?, ?, 0.09, 0.05, 0.025, 7, 0.0,
                       'narrow', 'stable', 'medium', ?, 'run-1')""",
            [asset_id, d, scenario, iv, price, (iv - price) / price, d],
        )
    conn.execute(
        "INSERT INTO factor_values VALUES (?, ?, 'liquidity_1m', ?)",
        [asset_id, d, liquidity],
    )


def test_review_attaches_gate_verdicts(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        seed_default_profile(conn)
        _seed_band_asset(conn, "LLY", "Healthcare", 500.0, 400.0, 5_000_000)
        result = run_opportunity_review(
            conn, methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=date(2026, 6, 23),
        )
    card = next(c for c in result.cards if c.asset_id == "LLY")
    assert card.risk_gate is not None
    assert card.risk_gate.status in {"pass", "warn", "block"}
    assert result.gate_summary is not None


def test_review_skips_gate_when_disabled(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        seed_default_profile(conn)
        _seed_band_asset(conn, "LLY", "Healthcare", 500.0, 400.0, 5_000_000)
        result = run_opportunity_review(
            conn, methodology_key="moat_adjusted_intrinsic_value",
            as_of_date=date(2026, 6, 23), apply_risk_gate=False,
        )
    assert all(c.risk_gate is None for c in result.cards)
    assert result.gate_summary is None
```

> Confirm the `intrinsic_value_bands` column list against `croesus/db/schema.sql` before running; adjust the INSERT to match exactly (Task 4's seed mirrors the C3 table). If `seed_default_profile` has a different name/signature, fix the import.

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/opportunities/test_review_risk_gate.py -v`
Expected: FAIL (`TypeError: run_opportunity_review() got an unexpected keyword argument 'apply_risk_gate'` or `risk_gate` attribute missing)

- [ ] **Step 3: Implement**

In `croesus/opportunities/review.py`:

Add imports:
```python
from dataclasses import dataclass, replace

from croesus.opportunities.risk_gate import (
    DEFAULT_MIN_LIQUIDITY_USD,
    RiskGateVerdict,
    evaluate_candidates,
)
```

Add the field to `OpportunityCard` (after `bear_case`):
```python
    bear_case: str | None
    risk_gate: RiskGateVerdict | None = None
```

Add the field to `OpportunityReviewResult` (after `recommendation_only`):
```python
    recommendation_only: bool = True
    gate_summary: dict[str, int] | None = None
```

Replace the `run_opportunity_review` signature and body tail:
```python
def run_opportunity_review(
    conn: duckdb.DuckDBPyConnection,
    *,
    methodology_key: str | None = None,
    methodology: OpportunityMethodology | None = None,
    as_of_date: date | None = None,
    limit: int = 20,
    portfolio_id: str = "default",
    profile_id: str = "default",
    apply_risk_gate: bool = True,
    min_liquidity_usd: float | None = DEFAULT_MIN_LIQUIDITY_USD,
) -> OpportunityReviewResult:
    if methodology is None:
        methodology = select_methodology(methodology_key)
    as_of = as_of_date or date.today()
    if methodology.key == "moat_adjusted_intrinsic_value":
        cards = _review_methodology_a(
            conn, methodology=methodology, as_of=as_of, limit=limit
        )
    else:  # pragma: no cover - guarded by select_methodology
        cards = []

    gate_summary: dict[str, int] | None = None
    if apply_risk_gate and cards:
        verdicts = evaluate_candidates(
            conn,
            [card.asset_id for card in cards],
            portfolio_id=portfolio_id,
            profile_id=profile_id,
            as_of_date=as_of,
            min_liquidity_usd=min_liquidity_usd,
        )
        if verdicts:
            cards = [replace(card, risk_gate=verdicts.get(card.asset_id)) for card in cards]
            gate_summary = {"pass": 0, "warn": 0, "block": 0}
            for card in cards:
                if card.risk_gate is not None:
                    gate_summary[card.risk_gate.status] += 1

    return OpportunityReviewResult(
        methodology=methodology,
        as_of_date=as_of,
        cards=cards,
        gate_summary=gate_summary,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/opportunities/test_review_risk_gate.py -v`
Expected: PASS

- [ ] **Step 5: Run existing opportunity review tests for regression**

Run: `python -m pytest tests/opportunities -v` (and any existing `tests/.../test_opportunity_review*.py`)
Expected: PASS (new optional field/kwarg does not break prior tests)

- [ ] **Step 6: Commit**

```bash
git add croesus/opportunities/review.py tests/opportunities/test_review_risk_gate.py
git commit -m "$(cat <<'EOF'
✨ feat: attach risk-gate verdicts to opportunity review cards

run_opportunity_review now runs the gate by default (portfolio/profile
"default"), attaching a verdict per card and a pass/warn/block summary.
Gate is skippable and degrades to ungated when no profile exists.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Render the gate in the report

**Files:**
- Modify: `croesus/reports/opportunity.py`
- Test: `tests/reports/test_opportunity_risk_gate_report.py`

**Interfaces:**
- Consumes: `OpportunityCard.risk_gate`, `OpportunityReviewResult.gate_summary` from Task 4.
- Produces: updated `render_opportunity_review` output containing a gate line per card and a summary line in the header.

**Behavior:** in the header, when `result.gate_summary is not None`, add `Risk gate: {pass} pass / {warn} warn / {block} block`. Per card, add a line: when `card.risk_gate is None` → `- Risk gate: —`; else `- Risk gate: {STATUS_UPPER} [{reason_codes joined}]` followed by each note as a sub-bullet.

- [ ] **Step 1: Write the failing test**

```python
# tests/reports/test_opportunity_risk_gate_report.py
from datetime import date

from croesus.opportunities.review import OpportunityCard, OpportunityReviewResult
from croesus.opportunities.risk_gate import RiskGateVerdict
from croesus.opportunities.selection import OPPORTUNITY_METHODOLOGIES
from croesus.reports.opportunity import render_opportunity_review


def _card(asset_id, verdict):
    return OpportunityCard(
        asset_id=asset_id, symbol=asset_id, name=None,
        methodology_key="moat_adjusted_intrinsic_value", as_of_date=date(2026, 6, 23),
        current_price=400.0, mechanical_intrinsic_value=420.0, mechanical_upside_pct=0.05,
        band_intrinsic_by_scenario={"bear": 300.0, "base": 500.0, "bull": 650.0},
        band_upside_by_scenario={"bear": -0.25, "base": 0.25, "bull": 0.6},
        base_upside_pct=0.25, thesis_as_of_date=date(2026, 6, 23),
        thesis_confidence="medium", evidence_source="filing",
        moat_grade="narrow", tech_grade="parity", sector_grade="stable",
        disruption_grade="medium", moat_evidence="x", tech_evidence="x",
        sector_evidence="x", disruption_evidence="x", bear_case="x",
        risk_gate=verdict,
    )


def test_report_renders_gate_status_and_summary():
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["moat_adjusted_intrinsic_value"],
        as_of_date=date(2026, 6, 23),
        cards=[
            _card("NVDA", RiskGateVerdict("block", ["SECTOR_OVER_MAX"], ["SECTOR_OVER_MAX: ..."])),
            _card("LLY", RiskGateVerdict("pass", [], [])),
        ],
        gate_summary={"pass": 1, "warn": 0, "block": 1},
    )
    out = render_opportunity_review(result)
    assert "Risk gate: 1 pass / 0 warn / 1 block" in out
    assert "Risk gate: BLOCK [SECTOR_OVER_MAX]" in out
    assert "Risk gate: PASS" in out


def test_report_renders_dash_when_gate_absent():
    result = OpportunityReviewResult(
        methodology=OPPORTUNITY_METHODOLOGIES["moat_adjusted_intrinsic_value"],
        as_of_date=date(2026, 6, 23),
        cards=[_card("LLY", None)],
        gate_summary=None,
    )
    out = render_opportunity_review(result)
    assert "Risk gate: —" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/reports/test_opportunity_risk_gate_report.py -v`
Expected: FAIL (gate strings not present in output)

- [ ] **Step 3: Implement**

In `croesus/reports/opportunity.py`, add a helper and wire it in:

```python
def _gate_lines(card: OpportunityCard) -> list[str]:
    gate = card.risk_gate
    if gate is None:
        return ["- Risk gate: —"]
    codes = f" [{', '.join(gate.reason_codes)}]" if gate.reason_codes else ""
    lines = [f"- Risk gate: {gate.status.upper()}{codes}"]
    lines.extend(f"  - {note}" for note in gate.notes)
    return lines
```

In `render_opportunity_review`, extend the header block (after the `Boundary:` line, before the trailing `""`):
```python
    if result.gate_summary is not None:
        s = result.gate_summary
        lines.append(
            f"Risk gate: {s['pass']} pass / {s['warn']} warn / {s['block']} block"
        )
```

In the per-card `lines.extend([...])`, add the gate lines right before the trailing `""`. Replace the closing of the per-card list:
```python
                f"- Bear case: {card.bear_case or 'n/a'}",
                *_gate_lines(card),
                "",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/reports/test_opportunity_risk_gate_report.py -v`
Expected: PASS

- [ ] **Step 5: Run existing opportunity report tests for regression**

Run: `python -m pytest tests/reports -k opportunity -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add croesus/reports/opportunity.py tests/reports/test_opportunity_risk_gate_report.py
git commit -m "$(cat <<'EOF'
✨ feat: render risk-gate verdict in opportunity report

Per-card PASS/WARN/BLOCK line with reason codes + notes, plus a gate
summary in the header. Ungated cards render an em-dash.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire CLI args

**Files:**
- Modify: `croesus/jobs/opportunity_review.py`
- Test: `tests/jobs/test_opportunity_review_cli.py` (extend if it exists; else create)

**Interfaces:**
- Consumes: `run_opportunity_review(..., portfolio_id, profile_id, apply_risk_gate, min_liquidity_usd)` from Task 4; `DEFAULT_MIN_LIQUIDITY_USD`.
- Produces: CLI flags `--portfolio-id` (default `"default"`), `--profile-id` (default `"default"`), `--no-risk-gate` (store_false → `apply_risk_gate`), `--min-liquidity-usd` (type `float`, default `DEFAULT_MIN_LIQUIDITY_USD`).

- [ ] **Step 1: Write the failing test**

```python
# tests/jobs/test_opportunity_review_cli.py  (add this test; keep existing ones)
from croesus.jobs.opportunity_review import _build_parser


def test_parser_has_risk_gate_args():
    parser = _build_parser()
    args = parser.parse_args(
        ["--methodology", "moat_adjusted_intrinsic_value",
         "--portfolio-id", "p1", "--profile-id", "pr1",
         "--no-risk-gate", "--min-liquidity-usd", "2000000"]
    )
    assert args.portfolio_id == "p1"
    assert args.profile_id == "pr1"
    assert args.apply_risk_gate is False
    assert args.min_liquidity_usd == 2000000.0


def test_parser_risk_gate_defaults():
    parser = _build_parser()
    args = parser.parse_args(["--methodology", "moat_adjusted_intrinsic_value"])
    assert args.portfolio_id == "default"
    assert args.profile_id == "default"
    assert args.apply_risk_gate is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/jobs/test_opportunity_review_cli.py -k risk_gate -v`
Expected: FAIL (`AttributeError: 'Namespace' object has no attribute 'portfolio_id'`)

- [ ] **Step 3: Implement**

In `croesus/jobs/opportunity_review.py`:

Add import:
```python
from croesus.opportunities.risk_gate import DEFAULT_MIN_LIQUIDITY_USD
```

In `_build_parser`, before `return parser`:
```python
    parser.add_argument("--portfolio-id", default="default", help="portfolio for the risk gate")
    parser.add_argument("--profile-id", default="default", help="profile for the risk gate")
    parser.add_argument(
        "--no-risk-gate", dest="apply_risk_gate", action="store_false",
        help="skip the portfolio risk-gate check",
    )
    parser.add_argument(
        "--min-liquidity-usd", type=float, default=DEFAULT_MIN_LIQUIDITY_USD,
        help="liquidity floor (21d mean $ volume); 0 disables the liquidity warn",
    )
```

In `main`, pass them through to `run_opportunity_review`:
```python
        result = run_opportunity_review(
            conn,
            methodology=methodology,
            as_of_date=as_of,
            limit=args.limit,
            portfolio_id=args.portfolio_id,
            profile_id=args.profile_id,
            apply_risk_gate=args.apply_risk_gate,
            min_liquidity_usd=args.min_liquidity_usd,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/jobs/test_opportunity_review_cli.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add croesus/jobs/opportunity_review.py tests/jobs/test_opportunity_review_cli.py
git commit -m "$(cat <<'EOF'
✨ feat: add risk-gate CLI flags to opportunity_review

--portfolio-id / --profile-id / --no-risk-gate / --min-liquidity-usd
thread the Phase E gate through the human-run review command.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Roadmap update + full regression

**Files:**
- Modify: `docs/planning/opportunity-engine-roadmap.md` (mark Phase E delivered; move E out of the deferred list)

- [ ] **Step 1: Update the roadmap**

In `docs/planning/opportunity-engine-roadmap.md`: add an `E` row to the Status table (Delivers: "Risk-gate integration over user-selected candidates: capacity + eligibility + liquidity, recommendation-only, rendered in the review report." Key code: `croesus/opportunities/risk_gate.py`, `croesus/portfolio/asset_attrs.py`). Update the pipeline diagram line `→ [E] risk gate integration (NOT BUILT)` to drop "(NOT BUILT)". In the deferred section, narrow the "E — risk gate integration" bullet to clarify only **automatic selection influence (구제/강등)** remains deferred (the recommendation-only gate is now built).

- [ ] **Step 2: Run the full test suite**

Run: `python -m pytest -q`
Expected: PASS (all green; prior count + the new tests). If any pre-existing unrelated failure appears, note it but do not fix in this plan.

- [ ] **Step 3: Commit**

```bash
git add docs/planning/opportunity-engine-roadmap.md
git commit -m "$(cat <<'EOF'
📝 docs: mark Phase E risk-gate integration delivered

Recommendation-only gate shipped; only automatic selection influence
(구제/강등) remains deferred.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- Gate semantics (capacity + eligibility + liquidity-warn) → Task 2. ✓
- `min_liquidity_usd` Phase-E floor + `0` disables → Task 2 (`evaluate_risk_gate`), Task 6 (CLI). ✓
- Orchestrator gathering inputs, empty-portfolio + missing-profile degradation → Task 3. ✓
- Shared `load_asset_attrs` extraction → Task 1. ✓
- `OpportunityCard.risk_gate` + `run_opportunity_review` integration + `gate_summary` → Task 4. ✓
- Report rendering → Task 5. ✓
- CLI args → Task 6. ✓
- No new tables / recommendation-only / no re-rank → respected (gate attaches to existing cards, never reorders; `recommendation_only` untouched). ✓
- Deferred: automatic selection influence + persistence → not implemented (Task 7 narrows the roadmap note). ✓

**Placeholder scan:** No TBD/TODO; every code step has full code. Two explicit "confirm before running" callouts (factor_values column order, intrinsic_value_bands column list, profile-seed API name) are verification instructions, not placeholders — the implementer must confirm DB schemas against `croesus/db/schema.sql` since this plan cannot see the live DDL.

**Type consistency:** `RiskGateVerdict(status, reason_codes, notes)` used identically across Tasks 2/4/5. `evaluate_risk_gate` and `evaluate_candidates` signatures match between definition (Tasks 2/3) and call sites (Tasks 3/4). `OpportunityCard.risk_gate` / `OpportunityReviewResult.gate_summary` field names consistent across Tasks 4/5. `DEFAULT_MIN_LIQUIDITY_USD` consistent across Tasks 2/4/6.

**Open verification items for the implementer (resolve in-task, not blockers):**
1. Exact `factor_values`, `intrinsic_value_bands`, and `assets` column lists vs `croesus/db/schema.sql` — adjust the test INSERTs.
2. Profile-seeding helper name in `croesus/profiles/seed_default_profile.py` (`seed_default_profile` assumed).
3. Existing test file paths/names for rebalance and opportunity-review CLI (Tasks 1/6).
