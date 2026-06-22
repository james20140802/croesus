# Phase E — Opportunity Risk-Gate Integration (Design)

**Status:** approved (2026-06-23), ready for implementation plan.
**Roadmap:** `docs/planning/opportunity-engine-roadmap.md` — Phase E ("사용자 선택
UI/리포트 + 위험 게이트 연동"). Phase D already shipped the user-selectable
methodology review surface; this spec covers the remaining **risk-gate
integration** piece.
**Authoritative parent spec:** `docs/superpowers/specs/2026-06-15-opportunity-research-engine-design.md`
(§ "사용자 선택 + 사람 게이트" line 130; § 보류 line 154).

---

## Goal

Take the user-selected opportunity candidates produced by `run_opportunity_review`
(Methodology A intrinsic-value bands) and check each against the **existing
portfolio risk gate** — concentration (집중도), liquidity (유동성), and profile
eligibility (프로파일) — surfacing a `pass / warn / block` verdict per candidate in
the **same review report**. Recommendation-only: the gate never proposes trades,
never re-ranks, never touches the portfolio layer. The human owns the decision
(philosophy §7).

An opportunity candidate is a **prospective new buy**, not a current holding. The
gate therefore asks "does the portfolio have *room* for this name, and is the name
*eligible*?" — **without simulating a position size**.

### Explicitly out of scope (deferred on purpose)

- **자동 선정 영향 (구제/강등)** — an opportunity thesis changing screen rank
  without a human. Parent spec defers this until validation accumulates. Phase E
  is recommendation/표기 only.
- **Position-sizing simulation** — no assumed weight, no headroom %. Capacity is
  judged from current bucket violations only (the `block_new_buy` semantics).
- **Persisting verdicts** — verdicts are recomputed at review time (derivable
  from holdings + profile + bands). No new table. Audit/forward-test
  persistence is a possible follow-up.

---

## Gate semantics (capacity + eligibility)

For each candidate, evaluate in priority order. Final `status` = `block` if any
block reason fires, else `warn` if any warn reason fires, else `pass`.

| Reason code | Condition | Status | Source of truth |
|-------------|-----------|--------|-----------------|
| `SECTOR_OVER_MAX` / `INDUSTRY_OVER_MAX` / `COUNTRY_OVER_MAX` / `CURRENCY_OVER_MAX` | candidate's bucket has `is_violation=True` in current exposures → no room for a new buy in that bucket | **block** | `compute_exposures` (fresh) |
| `POSITION_OVER_MAX` | candidate is **already held** and its position exposure is `is_violation` | **block** | `compute_exposures` |
| `DISALLOWED_ASSET_TYPE` | candidate `asset_type` ∈ `profile.disallowed_asset_types`, or ∉ `profile.allowed_asset_types` when that list is non-empty | **block** | `InvestorProfile` |
| `LIQUIDITY_BELOW_MINIMUM` | `liquidity_1m < min_liquidity_usd` | **warn** | `factor_values` + macro screening params |
| `ALREADY_HELD` | candidate `asset_id` ∈ current holdings | note only (no status change) | holdings |

**Why liquidity is WARN, not BLOCK (user-confirmed):** the screening engine hard-skips
illiquid names, but Phase E is recommendation-only and `min_liquidity_usd` is a
macro-tunable threshold, not a profile hard rule. Blocking on it could hide pearls;
flagging lets the human judge. Reason code is reused verbatim from screening.

**`min_liquidity_usd` source:** same path the screener uses — latest `MacroState`
→ `macro.screening_adapter.get_screening_params(macro_state)["filters"]["min_liquidity_usd"]`.
If no macro state is available, fall back to the screening adapter's module default.

**Empty portfolio / no snapshot:** no holdings → no exposures → all capacity checks
pass; only eligibility (asset_type) and liquidity apply. Degrade gracefully, never
crash.

---

## Components

### New: `croesus/opportunities/risk_gate.py`

```python
@dataclass(frozen=True)
class RiskGateVerdict:
    status: str                 # 'pass' | 'warn' | 'block'
    reason_codes: list[str]     # reused vocab: SECTOR_OVER_MAX, LIQUIDITY_BELOW_MINIMUM, ...
    notes: list[str]            # human-readable lines

# Pure decision function — no DB access.
def evaluate_risk_gate(
    candidate: AssetAttrs,
    *,
    exposures: list[Exposure],
    held_asset_ids: set[str],
    profile: InvestorProfile,
    liquidity_value: float | None,
    min_liquidity_usd: float | None,
) -> RiskGateVerdict: ...

# Orchestrator — gathers inputs from the DB, returns one verdict per candidate.
def evaluate_candidates(
    conn,
    candidate_asset_ids: Sequence[str],
    *,
    portfolio_id: str,
    profile_id: str,
    as_of_date: date,
) -> dict[str, RiskGateVerdict]: ...
```

`evaluate_candidates` responsibilities:
1. Load `InvestorProfile` + `PolicyTarget`s via `ProfileRepository`. If profile
   can't load → return empty dict (caller leaves `risk_gate=None`).
2. Load latest holdings via `PortfolioRepository.get_holdings(portfolio_id, as_of)`;
   determine the held set and `total_market_value` (snapshot may be absent → 0).
3. Load `AssetAttrs` for held assets **and** candidate assets (shared helper, below).
4. Build `ExposureLimits` from the profile; call `compute_exposures(...)` fresh.
5. Resolve `min_liquidity_usd` from latest macro state (fallback to default).
6. Load `liquidity_1m` per candidate from `factor_values` (latest ≤ as_of).
7. Call `evaluate_risk_gate(...)` per candidate; return the dict.

### Shared helper extraction (targeted improvement)

`rebalance_check._load_asset_attrs` (builds `dict[str, AssetAttrs]` from the
`assets` table) is currently private to the rebalance job. Extract it to a shared
location (e.g. `croesus/portfolio/asset_attrs.py`) and have both
`rebalance_check` and the new gate import it. No behavior change — pure move +
re-import.

### Changed: `croesus/opportunities/review.py`

- `OpportunityCard` gains `risk_gate: RiskGateVerdict | None = None` (trailing
  default → backward compatible; existing construction unaffected).
- `run_opportunity_review(..., portfolio_id="default", profile_id="default",
  apply_risk_gate=True)`: after assembling cards, when `apply_risk_gate` is set,
  call `evaluate_candidates` for the card asset_ids and attach verdicts. If the
  profile can't load, skip silently (cards keep `risk_gate=None`) and add a
  warning to the result.
- `OpportunityReviewResult` gains an optional gate summary (counts of
  pass/warn/block) for the report header.

### Changed: `croesus/reports/opportunity.py`

- `render_opportunity_review` adds a **Risk Gate** column to the card table
  (`PASS` / `WARN` / `BLOCK`) and a per-card detail line listing reason codes +
  notes. When `risk_gate is None` the column renders `—` (gate not run).
- Report header shows the gate summary counts.

### Changed: `croesus/jobs/opportunity_review.py`

- Add CLI args `--portfolio-id` (default `default`), `--profile-id` (default
  `default`), and `--no-risk-gate` (store_false → `apply_risk_gate`).
- Thread them through to `run_opportunity_review`.

---

## Data flow

```
run_opportunity_review(conn, methodology, as_of, portfolio_id, profile_id)
  → cards = _review_methodology_a(...)              # existing C3 band assembly
  → if apply_risk_gate:
       verdicts = evaluate_candidates(conn, [c.asset_id for c in cards], ...)
         ├─ ProfileRepository.get_profile(profile_id)         # profile + targets
         ├─ PortfolioRepository.get_holdings(portfolio_id, as_of)
         ├─ load_asset_attrs(conn, held ∪ candidate ids)      # shared helper
         ├─ compute_exposures(holdings, attrs, ExposureLimits.from(profile))
         ├─ min_liquidity_usd ← get_screening_params(latest macro_state)
         └─ liquidity_1m per candidate ← factor_values
       cards = [c.with_gate(verdicts.get(c.asset_id)) for c in cards]
  → OpportunityReviewResult(cards, gate_summary, recommendation_only=True)
```

No new tables. No persistence beyond the existing `reports` registration.

---

## Testing (TDD)

**Pure function (`evaluate_risk_gate`):**
- candidate in a violating sector → `block`, `SECTOR_OVER_MAX`.
- candidate already held with violating position exposure → `block`,
  `POSITION_OVER_MAX`.
- candidate `asset_type` in `disallowed_asset_types` → `block`,
  `DISALLOWED_ASSET_TYPE`.
- candidate `liquidity_1m` below `min_liquidity_usd` → `warn`,
  `LIQUIDITY_BELOW_MINIMUM`.
- clean candidate, room available → `pass`.
- empty portfolio (no exposures) → `pass` (eligibility only).
- precedence: a candidate tripping both a block and a warn reason → `block`.

**Orchestrator (`evaluate_candidates`):**
- seeded holdings + profile + candidates → correct per-asset verdicts.
- missing profile → empty dict (graceful skip).
- missing snapshot/holdings → eligibility-only verdicts, no crash.

**Integration (`run_opportunity_review`):**
- seeded `intrinsic_value_bands` + holdings + profile → cards carry verdicts.
- `apply_risk_gate=False` → cards keep `risk_gate=None` (unchanged behavior).

**Report (`render_opportunity_review`):**
- PASS / WARN / BLOCK rendered with reason codes; `None` → `—`.

**Regression:** full suite green; existing opportunity_review tests unaffected
(new field is optional, gate off-path preserves prior output).

---

## Reuse anchors

- `croesus/portfolio/exposure.py` — `compute_exposures`, `ExposureLimits`, `Exposure`.
- `croesus/profiles/models.py` — `InvestorProfile`, `validate_profile`.
- `croesus/profiles/repository.py` — `ProfileRepository.get_profile`.
- `croesus/portfolio/repository.py` — `PortfolioRepository.get_holdings`.
- `croesus/macro/screening_adapter.py` — `get_screening_params`.
- `factor_values` table — `liquidity_1m`.
- `croesus/reports/opportunity.py`, `croesus/jobs/opportunity_review.py` — house
  style for render/CLI.
