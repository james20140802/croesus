# Sprint 003c: Return-Anchored Profile Guidance

## Goal

Help a user who only knows "I want about X% per year" arrive at a complete,
internally consistent investor profile, by deterministically deriving what
that return realistically requires from the other profile fields.

```text
Desired Return (or Drawdown Tolerance)
  -> Risk-Return Mapping Table
  -> Implied Drawdown / Horizon / Allocation Posture
  -> Conflict Detection and Resolution Options
  -> Scenario Translation
  -> Profile Draft -> 003b Template Recommendation
```

Sprint 003 introduced profile storage and validation. Sprint 003b added
template-based policy onboarding. This sprint is an additive guidance layer in
front of both: it produces a consistent profile *draft*, then hands off to the
existing 003b template recommendation and the existing `save_profile()` write
path. It is a backward-compatible retrofit; no existing profile flow changes.

## Why This Exists

Croesus is profile-first: nearly every downstream decision is bounded by the
investor profile. That makes profile quality the single highest-leverage user
input — and it is exactly the input a novice cannot produce.

A novice can state a desired return. They cannot state:

- what drawdown that return has historically required;
- what minimum horizon makes that return statistically reasonable;
- whether their stated return and stated drawdown tolerance are a possible
  combination at all.

Sprint 003 validation already *rejects* impossible combinations (for example,
`expected_annual_return > 0.08` with `max_tolerable_drawdown > -0.05`
produces a warning). But rejection without guidance leaves the user guessing.
This sprint turns the validator's "no" into "here is what would have to be
true, pick which side to adjust."

## Scope

### 1. Risk-Return Mapping Table

Add a deterministic, documented mapping from return bands to historically
required risk posture. Stored as config, not code:

```yaml
# croesus/profiles/risk_return_map.yaml
bands:
  - name: capital_preservation
    expected_return_range: [0.02, 0.04]
    typical_equity_weight: [0.0, 0.3]
    historical_drawdown_range: [-0.05, -0.15]
    min_recommended_horizon_years: 2
    template_alias: defensive
  - name: balanced
    expected_return_range: [0.04, 0.065]
    typical_equity_weight: [0.3, 0.6]
    historical_drawdown_range: [-0.15, -0.30]
    min_recommended_horizon_years: 5
    template_alias: default
  - name: growth
    expected_return_range: [0.065, 0.085]
    typical_equity_weight: [0.6, 0.85]
    historical_drawdown_range: [-0.30, -0.45]
    min_recommended_horizon_years: 7
    template_alias: aggressive
  - name: equity_max
    expected_return_range: [0.085, 0.11]
    typical_equity_weight: [0.85, 1.0]
    historical_drawdown_range: [-0.45, -0.55]
    min_recommended_horizon_years: 10
    template_alias: aggressive
```

Rules:

- Band values are long-run historical heuristics, not promises. The config
  file must carry a comment block stating data sources and that values are
  editable assumptions.
- Returns above the highest band produce a deterministic warning that the
  target is outside what diversified public-market portfolios have
  historically delivered, and the guidance stops recommending (it does not
  invent leverage or concentration to hit the number).
- The LLM produces no numbers anywhere in this flow.

### 2. Bidirectional Anchoring

The user may anchor on either side:

```text
anchor = return:
  expected_annual_return -> implied drawdown floor, min horizon,
  equity-weight posture, template alias

anchor = drawdown:
  max_tolerable_drawdown -> realistic return ceiling, template alias
```

Both directions read the same mapping table, so the two flows can never give
contradictory guidance.

### 3. Conflict Detection and Resolution Options

When the user has stated values on both sides and they fall in incompatible
bands, present deterministic resolution options instead of only a warning:

```text
input: expected_annual_return = 0.10, max_tolerable_drawdown = -0.10

conflict: 10% return sits in equity_max (-45% ~ -55% historical drawdown);
          -10% tolerance sits in capital_preservation (2% ~ 4% return)

options:
  1. keep_drawdown  -> lower expected_annual_return to <= 0.04
  2. keep_return    -> accept max_tolerable_drawdown around -0.50
                       and investment_horizon_years >= 10
  3. meet_in_middle -> balanced band: return ~0.05-0.06, drawdown ~-0.25,
                       horizon >= 5
```

The user must explicitly pick an option (or edit values manually); guidance
never silently overwrites a stated value. The chosen draft then flows through
the existing Sprint 003 validation, which remains the final gate.

### 4. Scenario Translation

Translate abstract percentages into concrete amounts and historical episodes
so the user understands what they are agreeing to:

- If the user provides an approximate portfolio size, render drawdowns in
  currency: `-35% on 100,000,000 KRW => roughly 65,000,000 KRW at the worst
  point`.
- Show a small static table of historical drawdown episodes (for example
  2008, 2020, 2022) with the approximate drawdown a portfolio in the selected
  band would have experienced. Episode data is static config shipped with the
  mapping table, clearly labeled as approximate.

### 5. Derived Remaining Fields

Once return / drawdown / horizon are consistent, fill the remaining profile
fields from the matched band's template alias via the existing 003b template
layer (concentration caps, rebalance band, turnover, liquidity buffer). All
derived values are presented for review and remain editable before save.

### 6. CLI Integration

Extend the guided flow without breaking existing modes:

```bash
python -m croesus.jobs.profile_init --guided
```

- Add a return-first (or drawdown-first) entry question at the start of the
  guided flow.
- Existing `--interactive`, `--init-config`, and `--config` modes keep
  working unchanged; guidance is additive.
- Expose the guidance as a callable function returning a structured result so
  a future local UI can render the same flow without parsing CLI text.

## Data Models

### `ProfileGuidance`

```python
@dataclass(frozen=True)
class ProfileGuidance:
    anchor: str                      # "return" | "drawdown"
    matched_band: str
    implied_drawdown_range: tuple[float, float] | None
    implied_return_range: tuple[float, float] | None
    min_recommended_horizon_years: int
    template_alias: str
    scenarios: list[ScenarioLine]
    conflicts: list[GuidanceConflict]
    warnings: list[str]
```

### `GuidanceConflict`

```python
@dataclass(frozen=True)
class GuidanceConflict:
    field_a: str
    field_b: str
    description: str
    options: list[ResolutionOption]   # keep_a / keep_b / meet_in_middle drafts
```

## Suggested Files

```text
croesus/profiles/
  risk_return_map.yaml
  guidance.py

croesus/jobs/profile_init.py   # extend guided flow
```

Tests:

```text
tests/test_profile_guidance.py
tests/test_profile_init_job.py   # extend
```

## Acceptance Criteria

- A user who enters only a desired return receives, deterministically: an
  implied drawdown range, a minimum recommended horizon, an allocation
  posture, and a template alias.
- Anchoring on drawdown instead yields a realistic return ceiling from the
  same table, never a contradictory band.
- Impossible return/drawdown combinations produce explicit resolution
  options; the system never silently rewrites a user-stated value.
- Returns above the highest configured band warn instead of fabricating a
  riskier recommendation.
- Drawdowns are shown in concrete currency amounts when portfolio size is
  provided, plus approximate historical episode context.
- The final draft still passes through Sprint 003 validation as the only
  gate; guidance adds no new hard rejections.
- Existing `profile_init` modes and stored profiles are untouched.
- All numbers come from `risk_return_map.yaml`; no LLM-generated values.

## Out of Scope

- Monte Carlo simulation or probabilistic goal projections.
- Questionnaire-based risk scoring (psychometric risk profiling).
- Per-user historical backtesting of the proposed profile.
- Changing profile schema or validation invariants.
- Web UI (the guidance function must merely be UI-ready).
- Leveraged or concentrated strategies to reach above-band return targets.
