# Sprint 004c: Holdings Onboarding and Asset Resolver

## Goal

Let users import holdings with natural identifiers such as ticker symbols while
Croesus resolves, creates, and enriches internal `assets` registry rows behind
the scenes.

```text
Holdings CSV (symbol / asset_id)
  -> Asset Resolver
  -> Asset Registry Upsert
  -> Price History Bootstrap
  -> Portfolio Snapshot Input
```

This sprint follows Sprint 004b because mark-to-market needs reliable asset
currency and price lookup. It should be completed before Sprint 005 expands
screening beyond the seed assets.

Sprint 004b remains the completed mark-to-market baseline. This sprint should
not change the mark-to-market semantics. It improves how holdings become valid
snapshot input.

## Why This Exists

The `assets` table is a system registry, not a user-facing data-entry burden.
Users should not have to know or maintain internal IDs such as `US_EQ_AAPL` or
`US_ETF_VOO`. They should be able to provide holdings from a broker export or a
simple CSV, and the system should resolve the investment products into stable
internal records.

## Scope

### 1. Holdings CSV Input Upgrade

Support both internal and user-friendly identifiers:

```csv
portfolio_id,symbol,asset_id,quantity,avg_cost,currency,market_value
default,AAPL,,10,150,USD,
default,VOO,,5,430,USD,
default,,CASH_KRW,,,KRW,421391
```

Rules:

- `asset_id` remains supported for deterministic tests and advanced users.
- `symbol` is accepted when `asset_id` is blank.
- If both are provided, `asset_id` wins and `symbol` is used as a consistency
  check.
- Cash rows use `CASH_<CUR>` and bypass external lookup.
- The parsing and resolver path must be callable independently of the CLI so a
  future holdings-entry form can use the same validation and resolution logic.

### 2. Asset Resolver

Create a resolver that can turn a symbol into an `Asset` row.

Initial resolver behavior:

1. Check existing `assets` by `asset_id` or `symbol`.
2. If missing, query metadata from an external provider abstraction.
3. Normalize into internal asset fields:
   - `asset_id`
   - `symbol`
   - `name`
   - `asset_type`
   - `country`
   - `exchange`
   - `currency`
   - `sector`
   - `industry`
   - `metadata`
4. Upsert the resolved asset.
5. Return unresolved rows as warnings, not crashes.

The first provider may use yfinance metadata, but it must sit behind an
interface so the source can be replaced later.

### 3. Price Bootstrap

When a new asset is resolved, bootstrap enough price history for:

- latest close lookup;
- common factor calculation once enough history exists;
- mark-to-market in Sprint 004b.

This bootstrap should be idempotent and per-asset failure tolerant.

### 4. Resolver Status Output

The snapshot job should report what happened:

```text
asset resolver:
  resolved: AAPL -> US_EQ_AAPL
  created: VOO -> US_ETF_VOO
  unresolved: 005930.KS (missing provider metadata)
```

Persist detailed resolver metadata where useful, but keep user output concise.

The structured resolver result should include row-level status:

```text
resolved
created
unresolved
skipped
```

A future UI should be able to show the same result without parsing CLI text.

## Suggested Files

```text
croesus/assets/
  resolver.py
  metadata_provider.py

croesus/data_sources/
  yfinance_metadata.py

croesus/portfolio/
  import_holdings.py

croesus/jobs/
  portfolio_snapshot.py
```

Tests:

```text
tests/test_asset_resolver.py
tests/test_portfolio_snapshot.py
```

## Acceptance Criteria

- A holdings CSV can use `symbol` instead of internal `asset_id`.
- Unknown but resolvable symbols create `assets` rows automatically.
- Cash rows such as `CASH_USD` and `CASH_KRW` do not require registry metadata.
- New assets can be price-bootstrapped without hard-coded ticker lists.
- Resolver failures are shown as warnings and do not crash the entire snapshot.
- Screening still reads only from `assets`; it does not accept ad hoc ticker
  lists.
- The resolver returns structured status for each input row.

## Out of Scope

- Full all-US-listed universe ingestion.
- Paid security master integration.
- Broker account synchronization.
- Corporate action handling.
- Manual UI for editing asset metadata.
