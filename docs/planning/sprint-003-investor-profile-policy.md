# Sprint 003: Investor Profile and Policy Portfolio

## Goal

Implement the profile-first mandate layer that every future portfolio action must obey.

```text
Investor Profile
  -> Profile Validation
  -> Policy Portfolio Targets
  -> profile_init job
```

Sprint 001 and Sprint 002 are assumed complete. This sprint must not modify the existing macro pipeline beyond schema compatibility.

## Scope

### 1. Schema

Modify `croesus/db/schema.sql` to add:

- `investor_profiles`
- `policy_targets`

```sql
CREATE TABLE IF NOT EXISTS investor_profiles (
  profile_id TEXT PRIMARY KEY,
  name TEXT,
  base_currency TEXT,
  expected_annual_return DOUBLE,
  max_tolerable_drawdown DOUBLE,
  investment_horizon_years INTEGER,
  monthly_contribution DOUBLE,
  liquidity_buffer_months DOUBLE,
  allowed_asset_types JSON,
  disallowed_asset_types JSON,
  max_single_position_weight DOUBLE,
  max_sector_weight DOUBLE,
  max_industry_weight DOUBLE,
  max_theme_weight DOUBLE,
  max_country_weight DOUBLE,
  max_currency_weight DOUBLE,
  max_monthly_turnover DOUBLE,
  rebalance_band DOUBLE,
  trade_mode TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS policy_targets (
  profile_id TEXT NOT NULL,
  sleeve_name TEXT NOT NULL,
  target_weight DOUBLE NOT NULL,
  min_weight DOUBLE,
  max_weight DOUBLE,
  metadata JSON,
  PRIMARY KEY (profile_id, sleeve_name)
);
```

### 2. Profile Module

Create:

```text
croesus/profiles/
  __init__.py
  models.py
  repository.py
  validation.py
  seed_default_profile.py
```

Responsibilities:

- Represent an advanced investor profile.
- Represent policy target sleeves.
- Validate profile consistency.
- Store and load profiles from DuckDB.
- Seed one default advanced profile for local MVP use.

### 3. Job Entrypoint

Create:

```text
croesus/jobs/profile_init.py
```

Expected command:

```bash
python -m croesus.jobs.profile_init
```

Behavior:

1. Run migration.
2. Seed default profile.
3. Seed default policy targets.
4. Print profile ID and policy target summary.

## Data Models

### `InvestorProfile`

Closed-value fields use `str`-based enums (`Currency`, `AssetType`, `TradeMode`) — see ADR 0008.

```python
@dataclass(frozen=True)
class InvestorProfile:
    profile_id: str
    name: str
    base_currency: Currency
    expected_annual_return: float
    max_tolerable_drawdown: float
    investment_horizon_years: int
    monthly_contribution: float
    liquidity_buffer_months: float
    allowed_asset_types: list[AssetType]
    disallowed_asset_types: list[AssetType]
    max_single_position_weight: float
    max_sector_weight: float
    max_industry_weight: float
    max_theme_weight: float
    max_country_weight: float
    max_currency_weight: float
    max_monthly_turnover: float
    rebalance_band: float
    trade_mode: TradeMode
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `PolicyTarget`

```python
@dataclass(frozen=True)
class PolicyTarget:
    profile_id: str
    sleeve_name: str
    target_weight: float
    min_weight: float | None = None
    max_weight: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `ProfileValidationResult`

```python
@dataclass(frozen=True)
class ProfileValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]
```

## Validation Rules

Implement these in `croesus/profiles/validation.py`:

| Rule | Result |
|---|---|
| `expected_annual_return <= 0` | error |
| `max_tolerable_drawdown >= 0` | error |
| `investment_horizon_years < 1` | error |
| `rebalance_band <= 0` | error |
| `max_monthly_turnover <= 0` | error |
| `trade_mode` not in `{"propose_only", "approval_required"}` | error |
| `trade_mode == "bounded_auto"` | error in MVP |
| `max_single_position_weight > max_sector_weight` | warning |
| `max_tolerable_drawdown > -0.05 and expected_annual_return > 0.08` | warning |

Invalid profiles block portfolio action generation in later sprints. This sprint only needs to expose the validation result.

## Default Seed

`seed_default_profile.py` should insert:

```yaml
profile_id: default
name: Growth-oriented long-term taxable account
base_currency: USD
expected_annual_return: 0.10
max_tolerable_drawdown: -0.25
investment_horizon_years: 10
monthly_contribution: 2000
liquidity_buffer_months: 6
allowed_asset_types:
  - equity
  - etf
  - reit
  - cash
disallowed_asset_types:
  - option
  - leveraged_etf
  - short_position
max_single_position_weight: 0.10
max_sector_weight: 0.35
max_industry_weight: 0.25
max_theme_weight: 0.30
max_country_weight: 0.90
max_currency_weight: 0.95
max_monthly_turnover: 0.15
rebalance_band: 0.05
trade_mode: propose_only
```

Default policy targets:

| Sleeve | Target | Min | Max |
|---|---:|---:|---:|
| core_us_equity | 0.55 | 0.45 | 0.65 |
| satellite_equity | 0.15 | 0.00 | 0.20 |
| defensive_bonds | 0.20 | 0.10 | 0.30 |
| cash | 0.10 | 0.05 | 0.20 |

Targets must sum to 1.0. Add a validation helper or repository assertion to catch invalid seed data.

## Tests

Create:

```text
tests/test_profiles.py
tests/test_profile_init_job.py
```

Required tests:

1. `migrate()` creates `investor_profiles` and `policy_targets`.
2. `validate_profile()` accepts the default profile.
3. `validate_profile()` rejects `bounded_auto`.
4. `validate_profile()` rejects non-negative drawdown.
5. `validate_profile()` warns on unrealistic return/drawdown combination.
6. `ProfileRepository.upsert_profile()` round-trips JSON fields.
7. `ProfileRepository.upsert_policy_targets()` stores and loads targets.
8. `seed_default_profile()` is idempotent.
9. `python -m croesus.jobs.profile_init` completes on a temporary database path if the job exposes a callable `main` helper or `run_profile_init(conn)`.

Use temporary DuckDB files under `tmp_path` like the existing macro tests.

## Suggested Task Breakdown

### Task 1: Schema

Files:

- Modify: `croesus/db/schema.sql`
- Test: `tests/test_profiles.py`

Steps:

1. Add a failing test that runs `migrate(tmp_path / "profiles.duckdb")` and asserts both tables exist.
2. Add the two `CREATE TABLE IF NOT EXISTS` statements.
3. Run `pytest tests/test_profiles.py::test_migrate_creates_profile_tables -v`.
4. Commit:

```bash
git add croesus/db/schema.sql tests/test_profiles.py
git commit -m "🗃️ chore: add investor profile and policy target tables"
```

### Task 2: Models and Validation

Files:

- Create: `croesus/profiles/__init__.py`
- Create: `croesus/profiles/models.py`
- Create: `croesus/profiles/validation.py`
- Test: `tests/test_profiles.py`

Steps:

1. Add tests for valid default profile, invalid drawdown, invalid bounded automation, and warning-producing unrealistic profile.
2. Implement dataclasses.
3. Implement `validate_profile(profile) -> ProfileValidationResult`.
4. Run `pytest tests/test_profiles.py -v`.
5. Commit:

```bash
git add croesus/profiles tests/test_profiles.py
git commit -m "✨ feat: add investor profile validation"
```

### Task 3: Repository

Files:

- Create: `croesus/profiles/repository.py`
- Test: `tests/test_profiles.py`

Steps:

1. Add tests for profile JSON round-trip and policy target persistence.
2. Implement `ProfileRepository`.
3. Use `json.dumps` and `json.loads` consistently, matching `AssetRepository`.
4. Run `pytest tests/test_profiles.py -v`.
5. Commit:

```bash
git add croesus/profiles/repository.py tests/test_profiles.py
git commit -m "✨ feat: add profile repository"
```

### Task 4: Seed and Job

Files:

- Create: `croesus/profiles/seed_default_profile.py`
- Create: `croesus/jobs/profile_init.py`
- Test: `tests/test_profile_init_job.py`

Steps:

1. Add tests that seed default profile twice and verify one profile plus four policy targets.
2. Implement `seed_default_profile(conn)`.
3. Implement `run_profile_init(conn, log=print)` and `main()`.
4. Run `pytest tests/test_profile_init_job.py tests/test_profiles.py -v`.
5. Commit:

```bash
git add croesus/profiles/seed_default_profile.py croesus/jobs/profile_init.py tests/test_profile_init_job.py
git commit -m "✨ feat: add profile_init job"
```

## Acceptance Criteria

- `python -m croesus.jobs.profile_init` seeds a default advanced profile.
- Profile and policy target tables exist after migration.
- Default profile validates successfully.
- Invalid profiles produce deterministic errors.
- Warnings are available for unrealistic but not structurally invalid profiles.
- Policy targets round-trip through DuckDB.
- No Sprint 001 or Sprint 002 behavior changes.

## Out of Scope

- Holdings import.
- Portfolio exposure calculations.
- Screening changes.
- Rebalancing proposals.
- Brokerage integration.
- Automatic trade execution.
