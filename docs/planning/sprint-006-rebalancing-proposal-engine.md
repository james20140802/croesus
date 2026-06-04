# Sprint 006: Rebalancing Proposal Engine

## Goal

Deliver Level 1 MVP: deterministic portfolio rebalancing proposals.

```text
Investor Profile
  + Policy Targets
  + Portfolio Snapshot
  + MacroState
  + Screening Results
  -> Rebalancing Rules
  -> Proposed Actions
  -> Portfolio Action Report
```

This sprint produces recommendations only. It must not create broker orders or execute trades.

The structured proposal state is the product contract. Markdown and CSV reports
are views of that state, not the only output.

## Scope

### 1. Schema

Modify `croesus/db/schema.sql` to add:

- `rebalance_runs`
- `proposed_actions`

```sql
CREATE TABLE IF NOT EXISTS rebalance_runs (
  run_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  profile_id TEXT NOT NULL,
  date DATE NOT NULL,
  macro_regime TEXT,
  macro_positioning TEXT,
  decision TEXT,
  summary TEXT,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS proposed_actions (
  action_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  asset_id TEXT,
  sleeve_name TEXT,
  action_type TEXT NOT NULL,
  current_weight DOUBLE,
  target_weight DOUBLE,
  proposed_weight DOUBLE,
  estimated_trade_value DOUBLE,
  reason_codes JSON,
  human_readable_reason TEXT,
  requires_research BOOLEAN,
  requires_user_approval BOOLEAN
);
```

### 2. Rebalancing Module

Create:

```text
croesus/portfolio/rebalancing.py
croesus/portfolio/actions.py
```

Responsibilities:

- Load active investor profile.
- Load policy targets.
- Load latest portfolio snapshot, exposures, and drifts.
- Load latest MacroState.
- Load latest screening candidates.
- Apply deterministic action rules.
- Enforce turnover and concentration constraints.
- Persist proposed actions.

### 3. Report Module

Create:

```text
croesus/reports/
  __init__.py
  portfolio_action.py
```

If `croesus/reports/` already exists by implementation time, add only the new file.

Responsibilities:

- Render Markdown portfolio action report.
- Render CSV proposed action export.
- Keep report text deterministic and traceable to reason codes.
- Render only from persisted `rebalance_runs` and `proposed_actions` data.

### 4. Job Entrypoint

Create:

```text
croesus/jobs/rebalance_check.py
```

Expected command:

```bash
python -m croesus.jobs.rebalance_check
```

Behavior:

1. Run migration.
2. Load default portfolio/profile unless CLI options specify otherwise.
3. Load latest MacroState if present.
4. Load latest portfolio snapshot.
5. Load latest screening results.
6. Generate rebalance run and proposed actions.
7. Write Markdown and CSV reports.
8. Print summary.

Expose callable:

```python
def run_rebalance_check(
    conn,
    *,
    portfolio_id: str = "default",
    profile_id: str = "default",
    as_of_date: date | None = None,
    log=print,
) -> RebalanceRunResult:
    """Generate proposed actions, write portfolio action reports, and return the rebalance run result."""
```

`run_rebalance_check(...)` should be safe for a future local API or UI to call.
It should not rely on terminal prompts or parse CLI output from earlier jobs.

## Action Types

Use stable action types:

| Action | Meaning |
|---|---|
| `hold` | no trade needed |
| `trim` | reduce an overweight position |
| `add` | add to an underweight sleeve or candidate |
| `rebalance_to_band` | move a sleeve back inside policy range |
| `watch` | candidate requires monitoring or research |
| `block_new_buy` | do not add to an overexposed area |
| `raise_cash` | restore cash buffer |

## Reason Codes

Use stable reason codes:

```text
PROFILE_INVALID
POSITION_OVER_MAX
SECTOR_OVER_MAX
INDUSTRY_OVER_MAX
THEME_OVER_MAX
COUNTRY_OVER_MAX
CURRENCY_OVER_MAX
SLEEVE_OVER_BAND
SLEEVE_UNDER_BAND
CASH_BELOW_BUFFER
MACRO_CAUTIOUS_TIGHTEN_RISK
MACRO_DEFENSIVE_REDUCE_CONCENTRATION
FACTOR_SCORE_SUPPORTS_ADD
VALUATION_TOO_EXPENSIVE
QUALITATIVE_RESEARCH_REQUIRED
TURNOVER_LIMIT
NO_ACTION_WITHIN_POLICY
```

Reason codes are part of the product contract. Do not rename them casually once persisted.

## Decision Rules

Rules should run in order.

### Rule 1: Invalid Profile

If the active investor profile has validation errors:

- create one `hold` action with `PROFILE_INVALID`;
- do not propose adds or trims;
- report that no portfolio action can be generated until the profile is fixed.

### Rule 2: Position Concentration

For each `portfolio_exposures` row where:

```text
exposure_type == "position"
is_violation == true
```

propose:

```text
action_type = trim
proposed_weight = limit_weight
reason_codes = ["POSITION_OVER_MAX"]
```

Estimated trade value:

```text
(current_weight - proposed_weight) * portfolio_total_market_value
```

### Rule 3: Exposure Concentration

For each sector, industry, theme, country, or currency violation:

- create `block_new_buy` for that exposure;
- if violation is severe, also create trim suggestions for the largest holdings in that exposure.

Severe means:

```text
current_weight > limit_weight + profile.rebalance_band
```

Reason codes:

- `SECTOR_OVER_MAX`
- `INDUSTRY_OVER_MAX`
- `THEME_OVER_MAX`
- `COUNTRY_OVER_MAX`
- `CURRENCY_OVER_MAX`

### Rule 4: Policy Drift

For each `policy_drifts` row where `is_outside_band = true`:

- if current weight > max weight: propose `rebalance_to_band` down to target or max weight;
- if current weight < min weight: propose `rebalance_to_band` up to target or min weight.

Reason codes:

- `SLEEVE_OVER_BAND`
- `SLEEVE_UNDER_BAND`

### Rule 5: Cash Buffer

If cash sleeve is below min weight:

- propose `raise_cash`;
- do not propose new `add` actions until cash is restored.

Reason code:

- `CASH_BELOW_BUFFER`

### Rule 6: Macro Risk Posture

Map MacroState to action constraints:

| Macro positioning | Rule |
|---|---|
| `Aggressive` | allow add actions if profile and drift permit |
| `Moderately Aggressive` | allow normal add actions |
| `Neutral` | prefer policy drift only |
| `Cautious` | block new satellite adds unless they reduce risk |
| `Defensive` | prioritize trims, cash restoration, and defensive sleeves |

Reason codes:

- `MACRO_CAUTIOUS_TIGHTEN_RISK`
- `MACRO_DEFENSIVE_REDUCE_CONCENTRATION`

Macro cannot override profile limits.

### Rule 7: Candidate Adds

Use screening results only after rules 1 through 6.

Candidate add conditions:

- candidate bucket is `candidate`;
- candidate is not marked `blocked_by_portfolio_fit`;
- asset is not in overexposed sector/theme;
- policy sleeve is underweight or within allowed add range;
- turnover limit remains available;
- MacroState does not block new satellite exposure.

Reason code:

- `FACTOR_SCORE_SUPPORTS_ADD`

If candidate requires qualitative review before action, create `watch`, not `add`.

Reason code:

- `QUALITATIVE_RESEARCH_REQUIRED`

If candidate is quantitatively attractive but blocked by concentration, sleeve,
currency, macro posture, or valuation constraints, preserve it as `watch` or
`block_new_buy`; do not convert it into `add`.

### Rule 8: Turnover Limit

Total estimated trade value must not exceed:

```text
profile.max_monthly_turnover * portfolio_total_market_value
```

If proposed actions exceed the limit:

- keep highest-priority risk-reduction actions first;
- reduce or drop add actions;
- add `TURNOVER_LIMIT` to affected actions.

Priority order:

1. `PROFILE_INVALID`
2. `raise_cash`
3. `trim`
4. `rebalance_to_band`
5. `block_new_buy`
6. `watch`
7. `add`

### Rule 9: No Action

If no violations and no adds are justified:

- create a single `hold` action;
- reason code: `NO_ACTION_WITHIN_POLICY`.

## Data Models

### `ProposedAction`

```python
@dataclass(frozen=True)
class ProposedAction:
    action_id: str
    run_id: str
    asset_id: str | None
    sleeve_name: str | None
    action_type: str
    current_weight: float | None
    target_weight: float | None
    proposed_weight: float | None
    estimated_trade_value: float | None
    reason_codes: list[str]
    human_readable_reason: str
    requires_research: bool
    requires_user_approval: bool
```

### `RebalanceRunResult`

```python
@dataclass(frozen=True)
class RebalanceRunResult:
    run_id: str
    portfolio_id: str
    profile_id: str
    as_of_date: date
    decision: str
    actions: list[ProposedAction]
    markdown_report_path: Path | None
    csv_report_path: Path | None
```

Decision values:

```text
no_action
rebalance_recommended
profile_invalid
research_required
```

## Report Output

Write reports to:

```text
reports/portfolio_action_YYYY-MM-DD.md
reports/portfolio_action_YYYY-MM-DD.csv
```

Markdown structure:

```text
# Portfolio Action Report — YYYY-MM-DD

## Summary
- Portfolio:
- Profile:
- Macro posture:
- Decision:

## Current Issues
- NVDA is above the profile's max single-position weight.
- Technology is above the profile's max sector weight.
- Cash is below the policy minimum.

## Proposed Actions
1. Trim NVDA to the configured max single-position weight.
2. Raise cash to the policy minimum.
3. Move remaining proceeds to the underweight core equity sleeve.

## Blocked Actions
- Block new semiconductor satellite buys until theme exposure falls below the profile limit.

## Why
- The proposed actions reduce concentration, respect the current macro risk posture, and move the portfolio back inside policy bands.

## Data Used
- latest MacroState date
- latest portfolio snapshot date
- latest screening run ID
```

Reports should not use LLM output in Sprint 006.

The same summary sections should be derivable for a future review screen:

- current issues;
- proposed actions;
- blocked actions;
- watchlist candidates;
- data used;
- approval requirement.

## Tests

Create:

```text
tests/test_rebalancing.py
tests/test_portfolio_action_report.py
```

Required tests:

1. Invalid profile returns `profile_invalid` decision and no add/trim actions.
2. Position over max creates `trim`.
3. Sector over max creates `block_new_buy`.
4. Severe sector over max also suggests trimming largest holding in that sector.
5. Sleeve under min creates `rebalance_to_band`.
6. Cash under min creates `raise_cash` and blocks new adds.
7. Cautious MacroState blocks new satellite adds.
8. Defensive MacroState prioritizes concentration reduction.
9. Candidate add is created only when profile, policy, macro, and exposure rules allow it.
10. A high-scoring candidate blocked by portfolio fit becomes `watch` or
    `block_new_buy`, not `add`.
11. Turnover limit drops or reduces lower-priority actions.
12. No violations creates `hold` with `NO_ACTION_WITHIN_POLICY`.
13. Proposed actions are persisted in `proposed_actions`.
14. Markdown and CSV reports are generated.
15. `run_rebalance_check()` does not submit or prepare broker orders.

## Suggested Task Breakdown

### Task 1: Schema and Models

Files:

- Modify: `croesus/db/schema.sql`
- Create: `croesus/portfolio/actions.py`
- Test: `tests/test_rebalancing.py`

Steps:

1. Add failing migration tests for `rebalance_runs` and `proposed_actions`.
2. Add schema definitions.
3. Add `ProposedAction` and `RebalanceRunResult` dataclasses.
4. Run `pytest tests/test_rebalancing.py::test_migrate_creates_rebalance_tables -v`.
5. Commit:

```bash
git add croesus/db/schema.sql croesus/portfolio/actions.py tests/test_rebalancing.py
git commit -m "🗃️ chore: add rebalance proposal tables"
```

### Task 2: Rebalance Repository

Files:

- Modify: `croesus/portfolio/repository.py`
- Test: `tests/test_rebalancing.py`

Steps:

1. Add tests for storing a rebalance run and proposed actions.
2. Add repository methods:
   - `upsert_rebalance_run(run_id, portfolio_id, profile_id, as_of_date, decision, summary, metadata)`
   - `upsert_proposed_actions(actions)`
   - `load_latest_rebalance_run(portfolio_id)`
3. Use JSON serialization for reason codes and metadata.
4. Run `pytest tests/test_rebalancing.py -v`.
5. Commit:

```bash
git add croesus/portfolio/repository.py tests/test_rebalancing.py
git commit -m "✨ feat: persist rebalance proposals"
```

### Task 3: Core Rule Engine

Files:

- Create: `croesus/portfolio/rebalancing.py`
- Test: `tests/test_rebalancing.py`

Steps:

1. Add tests for invalid profile, position max, exposure max, policy drift, cash buffer, MacroState constraints, candidate adds, turnover limit, and no-action behavior.
2. Implement `generate_rebalance_proposal(conn, portfolio_id, profile_id, as_of_date)`.
3. Keep rule order deterministic.
4. Persist output through repository.
5. Run `pytest tests/test_rebalancing.py -v`.
6. Commit:

```bash
git add croesus/portfolio/rebalancing.py tests/test_rebalancing.py
git commit -m "✨ feat: generate deterministic rebalance proposals"
```

### Task 4: Report Generator

Files:

- Create: `croesus/reports/__init__.py`
- Create: `croesus/reports/portfolio_action.py`
- Test: `tests/test_portfolio_action_report.py`

Steps:

1. Add tests for Markdown sections and CSV columns.
2. Implement `render_portfolio_action_markdown(result, context)`.
3. Implement `write_portfolio_action_reports(result, output_dir=Path("reports"))`.
4. Run `pytest tests/test_portfolio_action_report.py -v`.
5. Commit:

```bash
git add croesus/reports tests/test_portfolio_action_report.py
git commit -m "✨ feat: render portfolio action reports"
```

### Task 5: Job

Files:

- Create: `croesus/jobs/rebalance_check.py`
- Test: `tests/test_rebalancing.py`

Steps:

1. Add an end-to-end test that seeds profile, policy, holdings, MacroState, and screening results.
2. Implement `run_rebalance_check()`.
3. Implement `main()` with optional `--portfolio-id`, `--profile-id`, `--date`, and `--output-dir`.
4. Run `pytest tests/test_rebalancing.py tests/test_portfolio_action_report.py -v`.
5. Commit:

```bash
git add croesus/jobs/rebalance_check.py tests/test_rebalancing.py
git commit -m "✨ feat: add rebalance_check job"
```

## Acceptance Criteria

- `python -m croesus.jobs.rebalance_check` produces a Markdown and CSV portfolio action report.
- Invalid profiles block action generation.
- Position, sector, industry, theme, country, currency, cash, and policy drift rules create deterministic actions.
- MacroState modifies risk posture but cannot override profile limits.
- Screening candidates can support add/watch actions only after profile and portfolio constraints pass.
- Turnover limit is enforced.
- A portfolio within policy generates a no-action report.
- No broker orders are generated.
- No trade execution code is introduced.

## Out of Scope

- Broker integration.
- Approval workflow.
- Bounded automation.
- Tax-aware lot selection.
- LLM research.
- Valuation factors.
- Optimization algorithms.
