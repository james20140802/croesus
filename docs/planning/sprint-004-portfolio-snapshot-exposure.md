# Sprint 004: Portfolio Snapshot and Exposure

## Goal

Represent the user's current portfolio and compute exposure, concentration, and policy drift.

```text
Manual Holdings Input
  -> Portfolio Holdings
  -> Latest Prices
  -> Current Weights
  -> Exposure and Drift
  -> portfolio_snapshot job
```

Sprint 003 is assumed complete. Sprint 001 and Sprint 002 planning files must not be modified.

## Scope

### 1. Schema

Modify `croesus/db/schema.sql` to add:

- `portfolios`
- `portfolio_holdings`
- `portfolio_snapshots`
- `portfolio_exposures`
- `policy_drifts`

```sql
CREATE TABLE IF NOT EXISTS portfolios (
  portfolio_id TEXT PRIMARY KEY,
  profile_id TEXT NOT NULL,
  name TEXT,
  base_currency TEXT,
  created_at TIMESTAMP,
  updated_at TIMESTAMP,
  metadata JSON
);

CREATE TABLE IF NOT EXISTS portfolio_holdings (
  portfolio_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  quantity DOUBLE,
  market_value DOUBLE,
  currency TEXT,
  cost_basis DOUBLE,
  source TEXT,
  metadata JSON,
  PRIMARY KEY (portfolio_id, asset_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  total_market_value DOUBLE,
  cash_value DOUBLE,
  metadata JSON,
  PRIMARY KEY (portfolio_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS portfolio_exposures (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  exposure_type TEXT NOT NULL,
  exposure_name TEXT NOT NULL,
  weight DOUBLE,
  market_value DOUBLE,
  limit_weight DOUBLE,
  is_violation BOOLEAN,
  PRIMARY KEY (portfolio_id, as_of_date, exposure_type, exposure_name)
);

CREATE TABLE IF NOT EXISTS policy_drifts (
  portfolio_id TEXT NOT NULL,
  as_of_date DATE NOT NULL,
  sleeve_name TEXT NOT NULL,
  current_weight DOUBLE,
  target_weight DOUBLE,
  min_weight DOUBLE,
  max_weight DOUBLE,
  drift DOUBLE,
  is_outside_band BOOLEAN,
  PRIMARY KEY (portfolio_id, as_of_date, sleeve_name)
);
```

### 2. Portfolio Module

Create:

```text
croesus/portfolio/
  __init__.py
  models.py
  repository.py
  import_holdings.py
  exposure.py
  policy.py
```

Responsibilities:

- Store portfolio metadata.
- Import manual holdings from CSV.
- Store holdings snapshots.
- Compute total value and position weights.
- Compute exposure by asset, sector, industry, theme, country, and currency.
- Compute drift from policy targets.

### 3. Job Entrypoint

Create:

```text
croesus/jobs/portfolio_snapshot.py
```

Expected command:

```bash
python -m croesus.jobs.portfolio_snapshot --holdings path/to/holdings.csv
```

For the first implementation, also expose a callable:

```python
def run_portfolio_snapshot(conn, holdings_path: Path, *, portfolio_id: str = "default", log=print) -> PortfolioSnapshotResult:
    """Import holdings, compute exposure and drift, persist the snapshot, and return the result."""
```

Tests should call the function directly with a temporary CSV.

## Holdings CSV Format

Minimum input:

```csv
portfolio_id,asset_id,quantity,market_value,currency,cost_basis
default,US_EQ_AAPL,10,1900,USD,1500
default,US_EQ_MSFT,5,2100,USD,1800
default,CASH_USD,1,1000,USD,1000
```

Rules:

- `portfolio_id` defaults to `default` if omitted.
- `market_value` is required for Level 1. Quantity-only valuation is future work.
- `currency` defaults to the profile base currency if omitted.
- Unknown `asset_id` should be reported and skipped unless it is `CASH_USD`.
- Cash should be represented as an asset-like row with `asset_id = CASH_USD`.

## Data Models

### `Portfolio`

```python
@dataclass(frozen=True)
class Portfolio:
    portfolio_id: str
    profile_id: str
    name: str
    base_currency: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `Holding`

```python
@dataclass(frozen=True)
class Holding:
    portfolio_id: str
    asset_id: str
    as_of_date: date
    quantity: float
    market_value: float
    currency: str
    cost_basis: float | None = None
    source: str | None = "manual_csv"
    metadata: dict[str, Any] = field(default_factory=dict)
```

### `Exposure`

```python
@dataclass(frozen=True)
class Exposure:
    portfolio_id: str
    as_of_date: date
    exposure_type: str
    exposure_name: str
    weight: float
    market_value: float
    limit_weight: float | None
    is_violation: bool
```

### `PolicyDrift`

```python
@dataclass(frozen=True)
class PolicyDrift:
    portfolio_id: str
    as_of_date: date
    sleeve_name: str
    current_weight: float
    target_weight: float
    min_weight: float | None
    max_weight: float | None
    drift: float
    is_outside_band: bool
```

### `PortfolioSnapshotResult`

```python
@dataclass(frozen=True)
class PortfolioSnapshotResult:
    portfolio_id: str
    as_of_date: date
    total_market_value: float
    holdings_imported: int
    holdings_skipped: int
    exposures: list[Exposure]
    policy_drifts: list[PolicyDrift]
    warnings: list[str]
```

## Exposure Logic

### Position Weight

```text
position_weight = holding.market_value / total_market_value
```

### Sector / Industry / Country / Currency

Use `assets` table columns:

- `sector`
- `industry`
- `country`
- `currency`

Cash should map to:

```text
sector = Cash
industry = Cash
country = profile base country if known, otherwise US
currency = profile base currency
```

### Theme Exposure

Read `assets.metadata.theme_tags` if present.

Example metadata:

```json
{
  "theme_tags": ["ai", "semiconductor"]
}
```

If no tags exist, skip theme exposure for that holding.

### Limit Checks

Compare exposures against the active profile:

| Exposure type | Profile field |
|---|---|
| position | `max_single_position_weight` |
| sector | `max_sector_weight` |
| industry | `max_industry_weight` |
| theme | `max_theme_weight` |
| country | `max_country_weight` |
| currency | `max_currency_weight` |

## Policy Drift Logic

Policy targets are sleeve-based. Use `policy_targets.metadata.asset_ids` or `metadata.asset_types` to map holdings to sleeves.

Initial default mapping:

```json
{
  "core_us_equity": {"asset_types": ["etf"], "theme_tags": ["broad_market"]},
  "satellite_equity": {"asset_types": ["equity"]},
  "defensive_bonds": {"asset_types": ["bond_etf"]},
  "cash": {"asset_ids": ["CASH_USD"]}
}
```

If a holding does not match any sleeve, classify it as `satellite_equity` for Level 1 and emit a warning.

For each sleeve:

```text
drift = current_weight - target_weight
is_outside_band = current_weight < min_weight or current_weight > max_weight
```

## Tests

Create:

```text
tests/test_portfolio_snapshot.py
tests/test_portfolio_exposure.py
```

Required tests:

1. `migrate()` creates all portfolio tables.
2. Holdings CSV imports valid rows.
3. Unknown asset is skipped with warning.
4. Cash row is accepted.
5. Position weights sum to approximately 1.0.
6. Sector exposure aggregates multiple holdings.
7. Theme exposure reads `assets.metadata.theme_tags`.
8. Exposure violations compare against profile limits.
9. Policy drift identifies sleeve outside min/max band.
10. `run_portfolio_snapshot()` writes `portfolio_snapshots`, `portfolio_exposures`, and `policy_drifts`.

## Suggested Task Breakdown

### Task 1: Schema

Files:

- Modify: `croesus/db/schema.sql`
- Test: `tests/test_portfolio_snapshot.py`

Steps:

1. Add a failing migration test for the five new tables.
2. Add the table definitions.
3. Run `pytest tests/test_portfolio_snapshot.py::test_migrate_creates_portfolio_tables -v`.
4. Commit:

```bash
git add croesus/db/schema.sql tests/test_portfolio_snapshot.py
git commit -m "🗃️ chore: add portfolio snapshot tables"
```

### Task 2: Models and Repository

Files:

- Create: `croesus/portfolio/__init__.py`
- Create: `croesus/portfolio/models.py`
- Create: `croesus/portfolio/repository.py`
- Test: `tests/test_portfolio_snapshot.py`

Steps:

1. Add tests for upserting portfolio, holdings, snapshot, exposures, and drifts.
2. Implement dataclasses.
3. Implement `PortfolioRepository`.
4. Run `pytest tests/test_portfolio_snapshot.py -v`.
5. Commit:

```bash
git add croesus/portfolio tests/test_portfolio_snapshot.py
git commit -m "✨ feat: add portfolio repository"
```

### Task 3: Holdings Import

Files:

- Create: `croesus/portfolio/import_holdings.py`
- Test: `tests/test_portfolio_snapshot.py`

Steps:

1. Add tests with temporary CSV files for valid holdings, omitted `portfolio_id`, cash row, and unknown asset skip.
2. Implement `load_holdings_csv(path, conn, as_of_date)`.
3. Run `pytest tests/test_portfolio_snapshot.py -v`.
4. Commit:

```bash
git add croesus/portfolio/import_holdings.py tests/test_portfolio_snapshot.py
git commit -m "✨ feat: import manual portfolio holdings"
```

### Task 4: Exposure and Drift

Files:

- Create: `croesus/portfolio/exposure.py`
- Create: `croesus/portfolio/policy.py`
- Test: `tests/test_portfolio_exposure.py`

Steps:

1. Add tests for position, sector, theme, country, currency, and policy drift.
2. Implement exposure aggregation functions.
3. Implement policy sleeve mapping and drift calculation.
4. Run `pytest tests/test_portfolio_exposure.py -v`.
5. Commit:

```bash
git add croesus/portfolio/exposure.py croesus/portfolio/policy.py tests/test_portfolio_exposure.py
git commit -m "✨ feat: compute portfolio exposure and policy drift"
```

### Task 5: Job

Files:

- Create: `croesus/jobs/portfolio_snapshot.py`
- Test: `tests/test_portfolio_snapshot.py`

Steps:

1. Add an end-to-end test that seeds profile, policy targets, assets, and a holdings CSV.
2. Implement `run_portfolio_snapshot()`.
3. Implement CLI argument parsing for `--holdings`, `--portfolio-id`, and optional `--date`.
4. Run `pytest tests/test_portfolio_snapshot.py tests/test_portfolio_exposure.py -v`.
5. Commit:

```bash
git add croesus/jobs/portfolio_snapshot.py tests/test_portfolio_snapshot.py
git commit -m "✨ feat: add portfolio_snapshot job"
```

## Acceptance Criteria

- Manual holdings can be imported without broker integration.
- Portfolio snapshot total market value is stored.
- Exposure rows are stored by position, sector, industry, theme, country, and currency.
- Policy drift rows are stored by sleeve.
- Concentration violations are deterministic.
- Missing or unknown assets do not crash the full snapshot.
- No trade proposals are generated in this sprint.

## Out of Scope

- Screening.
- Rebalancing actions.
- Broker integration.
- Tax lots.
- Quantity-only market value calculation.
- Multi-currency FX conversion.
