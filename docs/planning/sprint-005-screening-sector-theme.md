# Sprint 005: Screening and Sector/Theme Analysis

## Goal

Turn deterministic factor values into candidate rankings and portfolio-aware sector/theme analysis.

```text
factor_values
  -> percentile normalization
  -> macro-adjusted factor weights
  -> screening_results
  -> sector/theme analysis inputs
```

Sprint 005 produces candidates and exposure intelligence. It does not create rebalance actions or trades.

The output should distinguish between an asset that is quantitatively
attractive and an asset that is currently addable to the user's portfolio.

## Scope

### 1. Screening Module

Create:

```text
croesus/screening/
  __init__.py
  models.py
  repository.py
  normalization.py
  run_screening.py
  sector_theme.py
```

Responsibilities:

- Load active assets from `assets`.
- Load latest factor values for each asset.
- Normalize factors within the current universe.
- Apply macro-adjusted screening weights.
- Apply basic eligibility filters.
- Store ranked candidates in `screening_results`.
- Aggregate sector and theme scores for portfolio-aware reporting.
- Store portfolio-fit metadata when current exposure data exists, including
  whether a candidate would worsen an existing profile violation.

### 2. Schema

The existing `screening_results` table already exists:

```sql
CREATE TABLE IF NOT EXISTS screening_results (
  run_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  score DOUBLE,
  rank INTEGER,
  decision_bucket TEXT,
  reason TEXT,
  PRIMARY KEY (run_id, asset_id)
);
```

Modify it only if necessary. If richer structured reasons are required, add optional columns without breaking existing tests:

```sql
ALTER TABLE screening_results ADD COLUMN IF NOT EXISTS reason_codes JSON;
ALTER TABLE screening_results ADD COLUMN IF NOT EXISTS factor_scores JSON;
ALTER TABLE screening_results ADD COLUMN IF NOT EXISTS metadata JSON;
```

### 3. Job Entrypoint

Create:

```text
croesus/jobs/screening_run.py
```

Expected command:

```bash
python -m croesus.jobs.screening_run
```

Behavior:

1. Run migration.
2. Load latest MacroState if present.
3. Fall back to neutral screening params if absent.
4. Run screening over active assets.
5. Store `screening_results`.
6. Print top candidates and skipped assets.

Also expose:

```python
def run_screening_job(conn, *, as_of_date: date | None = None, log=print) -> ScreeningRunResult:
    """Load screening params, rank active assets, persist screening_results, and return the run result."""
```

## Factor Inputs

Initial factors:

| Factor | Direction | Notes |
|---|---|---|
| `momentum_1m` | higher is better | recent trend |
| `momentum_3m` | higher is better | medium trend |
| `momentum_6m` | higher is better | medium trend |
| `liquidity_1m` | higher is better | tradability |
| `above_200d_ma` | higher is better | 0 or 1 |
| `volatility_3m` | lower is better | penalty |

Use these score groups:

```text
momentum_score = average(percentile(momentum_1m), percentile(momentum_3m), percentile(momentum_6m))
liquidity_score = percentile(liquidity_1m)
trend_score = percentile(above_200d_ma)
volatility_penalty = percentile(volatility_3m)
```

The total score should follow the active screening params:

```text
score =
  weight(momentum) * momentum_score
+ weight(liquidity) * liquidity_score
+ weight(trend) * trend_score
- weight(volatility_penalty) * volatility_penalty
```

Weights come from `croesus.macro.screening_adapter.get_screening_params(state)` or `neutral_screening_params()`.

## Normalization

Implement percentile ranking in `croesus/screening/normalization.py`.

Rules:

- Percentiles are computed within the screening universe.
- Null values remain null and do not receive a score.
- If all values for a factor are null, that factor contributes no score.
- Ties receive the average percentile.
- Percentile range should be `0.0` to `1.0`.

## Eligibility Filters

Initial filters:

- Active assets only.
- Asset type in `equity` or `etf` for Level 1.
- If screening params include `filters.min_liquidity_usd`, skip assets below it.
- If screening params include `filters.max_volatility_3m`, skip assets above it.
- If an asset has fewer than three non-null score groups, skip it.

Do not hard-code ticker lists.

## Decision Buckets

Use deterministic buckets:

| Bucket | Rule |
|---|---|
| `candidate` | rank <= `candidate_count` |
| `watch` | rank > `candidate_count` and score is not null |
| `blocked_by_portfolio_fit` | attractive score but current portfolio constraints block new buys |
| `skipped` | insufficient factors or failed eligibility |

`screening_results.reason` should be human-readable but deterministic.

Examples:

```text
ranked by macro-adjusted factor score
skipped: missing momentum factors
skipped: liquidity below macro-adjusted minimum
blocked: Technology exposure already exceeds profile max
```

## Sector and Theme Analysis

`sector_theme.py` should compute aggregated scores:

```text
sector_score = average(candidate scores by assets.sector)
industry_score = average(candidate scores by assets.industry)
theme_score = average(candidate scores by assets.metadata.theme_tags)
```

Also compute current exposure overlay if Sprint 004 data exists:

```text
sector_candidate_score
current_sector_weight
profile_sector_limit
is_overexposed
```

Overexposed sectors/themes should not disappear from the report. They should be flagged so Sprint 006 can block or trim actions.

When an asset has a high score but belongs to an overexposed sector, industry,
theme, country, or currency, screening should preserve it as a watch/blocked
candidate instead of presenting it as an addable candidate.

## Data Models

### `ScreeningCandidate`

```python
@dataclass(frozen=True)
class ScreeningCandidate:
    run_id: str
    asset_id: str
    score: float | None
    rank: int | None
    decision_bucket: str
    reason: str
    reason_codes: list[str] = field(default_factory=list)
    factor_scores: dict[str, float | None] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
```

Candidate metadata should be app-ready and may include:

```python
{
    "portfolio_fit": "addable" | "watch" | "blocked",
    "blocking_exposures": ["sector:Technology"],
    "would_worsen_violation": True,
}
```

### `ScreeningRunResult`

```python
@dataclass(frozen=True)
class ScreeningRunResult:
    run_id: str
    as_of_date: date
    candidates: list[ScreeningCandidate]
    skipped: list[ScreeningCandidate]
    screening_params: dict[str, Any]
```

### `SectorThemeScore`

```python
@dataclass(frozen=True)
class SectorThemeScore:
    exposure_type: str
    exposure_name: str
    score: float
    asset_count: int
    current_weight: float | None = None
    limit_weight: float | None = None
    is_overexposed: bool = False
```

## Tests

Create:

```text
tests/test_screening.py
tests/test_sector_theme.py
```

Required tests:

1. Percentile normalization returns values between `0.0` and `1.0`.
2. Percentile normalization handles ties.
3. Screening skips inactive assets.
4. Screening reads factors from `factor_values`.
5. Screening applies neutral weights when no MacroState exists.
6. Screening applies MacroState weights when one exists.
7. Screening stores candidate rows in `screening_results`.
8. Missing factors produce `skipped` rows or skipped result entries.
9. Sector scores aggregate asset scores.
10. Theme scores read `assets.metadata.theme_tags`.
11. Overexposed sector is flagged when Sprint 004 exposure rows exist.
12. High-scoring candidates in overexposed areas are persisted as blocked/watch
    candidates, not addable candidates.

## Suggested Task Breakdown

### Task 1: Screening Models and Normalization

Files:

- Create: `croesus/screening/__init__.py`
- Create: `croesus/screening/models.py`
- Create: `croesus/screening/normalization.py`
- Test: `tests/test_screening.py`

Steps:

1. Add failing tests for percentile range, ties, and null handling.
2. Implement dataclasses.
3. Implement `percentile_rank(values: Mapping[str, float | None])`.
4. Run `pytest tests/test_screening.py::test_percentile_rank_handles_ties -v`.
5. Commit:

```bash
git add croesus/screening tests/test_screening.py
git commit -m "✨ feat: add screening normalization"
```

### Task 2: Screening Repository

Files:

- Modify: `croesus/db/schema.sql` if adding optional result columns
- Create: `croesus/screening/repository.py`
- Test: `tests/test_screening.py`

Steps:

1. Add tests for storing and loading `ScreeningCandidate` rows.
2. Add optional `reason_codes`, `factor_scores`, and `metadata` columns if needed.
3. Implement `ScreeningRepository.upsert_results()`.
4. Run `pytest tests/test_screening.py -v`.
5. Commit:

```bash
git add croesus/db/schema.sql croesus/screening/repository.py tests/test_screening.py
git commit -m "✨ feat: add screening results repository"
```

### Task 3: Run Screening

Files:

- Create: `croesus/screening/run_screening.py`
- Test: `tests/test_screening.py`

Steps:

1. Add tests that seed assets and factor values, then assert ranked candidates.
2. Add tests for neutral params and MacroState params.
3. Implement `run_screening(conn, screening_params, as_of_date=None)`.
4. Store results through `ScreeningRepository`.
5. Run `pytest tests/test_screening.py -v`.
6. Commit:

```bash
git add croesus/screening/run_screening.py tests/test_screening.py
git commit -m "✨ feat: rank assets with macro-adjusted screening"
```

### Task 4: Sector and Theme Scores

Files:

- Create: `croesus/screening/sector_theme.py`
- Test: `tests/test_sector_theme.py`

Steps:

1. Add tests for sector aggregation from `assets.sector`.
2. Add tests for theme aggregation from `assets.metadata.theme_tags`.
3. Add test for overexposed sector flag using `portfolio_exposures`.
4. Implement `compute_sector_theme_scores(conn, run_id, portfolio_id=None, as_of_date=None)`.
5. Run `pytest tests/test_sector_theme.py -v`.
6. Commit:

```bash
git add croesus/screening/sector_theme.py tests/test_sector_theme.py
git commit -m "✨ feat: aggregate sector and theme screening scores"
```

### Task 5: Job

Files:

- Create: `croesus/jobs/screening_run.py`
- Test: `tests/test_screening.py`

Steps:

1. Add an end-to-end test for `run_screening_job()` with seeded factors.
2. Implement `run_screening_job()`.
3. Implement `main()`.
4. Run `pytest tests/test_screening.py tests/test_sector_theme.py -v`.
5. Commit:

```bash
git add croesus/jobs/screening_run.py tests/test_screening.py
git commit -m "✨ feat: add screening_run job"
```

## Acceptance Criteria

- `python -m croesus.jobs.screening_run` produces ranked screening results.
- Screening uses active assets from the registry.
- Screening uses factor values from DuckDB, not LLM output.
- MacroState adjusts screening params when available.
- Neutral params are used when MacroState is absent.
- Sector and theme scores are computed.
- Overexposed sectors/themes are flagged but not automatically traded.
- No rebalance actions or orders are generated.

## Out of Scope

- Investor profile creation.
- Portfolio snapshot computation.
- Rebalancing proposals.
- Valuation factors.
- LLM research.
- Trade execution.
