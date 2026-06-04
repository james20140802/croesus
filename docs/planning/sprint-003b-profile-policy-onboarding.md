# Sprint 003b: Guided Profile and Policy Onboarding

## Goal

Make investor-profile and policy-portfolio setup usable without requiring the
user to hand-author every policy sleeve and target range.

```text
Profile Inputs
  -> Validation
  -> Policy Template Recommendation
  -> Editable Policy Targets
  -> Saved Profile + Policy
```

Sprint 003 already introduced the profile and policy tables. This sprint is an
additive onboarding layer on top of that implementation. If Sprint 004 is
already complete, this sprint should be implemented as a backward-compatible
retrofit: do not rewrite existing profile or portfolio snapshot behavior.

## Why This Exists

For a local portfolio OS, the user should define personal constraints, not
manually design a policy portfolio from scratch. It is reasonable to ask the
user for expected return, drawdown tolerance, horizon, liquidity needs, and
asset-class preferences. It is not reasonable to require every user to know
what `core_us_equity = 0.55`, `satellite_equity = 0.15`, and cash bands should
be before the system can operate.

## Scope

### 1. Policy Template Model

Add a small template layer that converts profile characteristics into initial
policy targets.

Example templates:

| Template | Typical Use |
|---|---|
| `growth_long_term` | Long horizon, higher drawdown tolerance |
| `balanced_long_term` | Moderate return and drawdown expectations |
| `capital_preservation` | Lower drawdown tolerance, higher cash/defensive sleeve |

Expose user-facing aliases for these templates:

| User-facing alias | Template |
|---|---|
| `aggressive` | `growth_long_term` |
| `default` | `balanced_long_term` |
| `defensive` | `capital_preservation` |

The template should output editable `PolicyTarget` rows, not opaque optimizer
results.

### 2. Guided Setup Flow

Extend `profile_init` without breaking the current CLI:

```bash
python -m croesus.jobs.profile_init --guided
```

Behavior:

1. Ask for or load profile inputs.
2. Validate structural errors and warnings.
3. Recommend a policy template.
4. Print the proposed sleeve allocation and ranges.
5. Save only after explicit confirmation in interactive mode, or via a
   deterministic non-interactive flag in tests.
6. Return a structured recommendation summary so a future local UI can render
   the same review step without parsing CLI text.

The existing `--interactive`, `--init-config`, and `--config` modes must keep
working.

### 3. Policy Target Validation UX

Improve validation output so the user sees actionable messages:

- Target weights must sum to 1.0.
- Min/target/max must be ordered.
- Missing cash sleeve should be warned or filled from template.
- Sleeve metadata should map common asset classes such as equity, ETF, bond ETF,
  and cash.

### 4. Migration / Retrofit Rule

This sprint can be run after Sprint 004 has been implemented. The retrofit rule
is:

- Do not change existing profile IDs.
- Do not delete user-created policy targets unless the user explicitly saves a
  replacement policy.
- Keep `ProfileRepository.save_profile()` as the single write path for replacing
  policy targets atomically.
- Existing portfolio snapshots remain valid; future snapshots use the updated
  policy targets.

## Data Models

### `PolicyTemplate`

```python
@dataclass(frozen=True)
class PolicyTemplate:
    template_id: str
    name: str
    description: str
    targets: list[PolicyTarget]
    warnings: list[str] = field(default_factory=list)
```

### `PolicyRecommendation`

```python
@dataclass(frozen=True)
class PolicyRecommendation:
    profile_id: str
    template_id: str
    targets: list[PolicyTarget]
    rationale: list[str]
    warnings: list[str]
```

## Suggested Files

```text
croesus/profiles/
  policy_templates.py
  onboarding.py

croesus/jobs/profile_init.py
```

Tests:

```text
tests/test_profile_onboarding.py
tests/test_profile_init_job.py
```

## Acceptance Criteria

- A user can create a valid profile and policy without hand-writing target
  sleeve percentages.
- A user can select `default`, `aggressive`, or `defensive` without knowing
  internal template IDs.
- The generated policy targets are explicit rows in `policy_targets`.
- Existing `profile_init` modes keep working.
- Running this after Sprint 004 does not invalidate existing snapshots.
- Invalid or inconsistent profile inputs produce deterministic errors or
  warnings.
- No rebalancing, screening, or trade execution logic is introduced.

## Out of Scope

- Mean-variance optimization.
- Personalized questionnaire scoring beyond the explicit profile inputs.
- Broker integration.
- Automatic trade execution.
- Web UI.
