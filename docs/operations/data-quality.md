# Data Quality and Asset-Type Routing

Sprint 008a closes the gap between what the investor profile *accepts* and what
the pipeline actually *tracks*. Before it, only US equities (+ the SPY
benchmark) were priced and factored; everything else a user held — a bond ETF,
a Korean stock, BTC, KRW cash — degraded silently:

| before | after |
|---|---|
| KRW cash valued 1:1 to USD on the first snapshot (~1400x overstatement) | missing FX rates are fetched on demand during the snapshot; if the fetch fails, an `FX_MISSING` **ERROR** is persisted and the snapshot is reported **DEGRADED** |
| international / crypto / fund holdings price-refreshed once (resolver bootstrap), then valued at cost forever | every asset whose type is in `PRICEABLE_ASSET_TYPES` refreshes daily and gets the six common price factors |
| every ETF stored as `asset_type="etf"`, so the `defensive_bonds` sleeve could never match and bond ETFs fell into `satellite_equity` | the classifier refines `etf` → `bond_etf` / `reit_etf` / `leveraged_etf` (and `cryptocurrency` → `crypto`) on resolve; `backfill_asset_types` fixes pre-existing rows |
| fallbacks surfaced only as transient warning strings | every fallback is a persistent row in `data_quality_issues`, and ERROR-level issues lead the portfolio-action and performance reports |

## The loud-failure contract

`croesus.fx.convert.to_base` now raises `FxRateMissing` instead of silently
using a 1:1 rate. The only caller allowed to pass `fallback_to_one=True` is
`mark_to_market`, and only *after* recording an ERROR issue — the number still
exists so the snapshot completes, but it is never presented as clean:

- `PRICE_MISSING` (ERROR): no stored close → manual market_value or cost-basis
  fallback used.
- `FX_MISSING` (ERROR): no rate for a holding currency → 1:1 passthrough used,
  holding metadata gains `"fx_missing": true`.
- `QUANTITY_MISSING` (WARN): quantity absent → manual market_value used.

Issues are written by `DataQualityRepository` into `data_quality_issues`.
Reports call `croesus.quality.report_block.data_quality_block(conn)` and lead
with a `## ⚠️ Data Quality — DEGRADED` section whenever ERROR rows exist in the
last 48h.

## Asset-type taxonomy

`croesus/assets/classifier.py` is the single source of truth:

- `PRICEABLE_ASSET_TYPES` — `{equity, etf, bond_etf, reit_etf, reit,
  leveraged_etf, crypto, fund}` — shared by price ingestion *and* common-factor
  computation so the two cannot drift. Cash and options are excluded (no daily
  close series).
- `classify_asset_type(asset)` — refines the coarse yfinance type using the
  asset name + yfinance `category` metadata. Leveraged keywords outrank bond
  keywords ("Direxion Daily 20+ Year Treasury Bull 3X" is `leveraged_etf`,
  not `bond_etf`). **Only `asset_type` changes; `asset_id` is a stable primary
  key and is never rewritten** (AGG stays `US_ETF_AGG` with
  `asset_type=bond_etf`).

Valuation (multiples + DCF) remains **equity-only by design** — bond ETFs and
crypto have no defensible FCF-based intrinsic value; they get price factors
only.

One-time migration for assets registered before the classifier existed:

```bash
python -m croesus.jobs.backfill_asset_types                     # DB-only, idempotent
python -m croesus.jobs.backfill_asset_types --refresh-metadata  # also refetch yfinance category
```

## Scheduler registration

`local_sync` now wires two previously manual-only jobs:

- `quarterly_run` (domain `fundamentals`, stale after ~92 days — statements
  change on the filing cadence). It deliberately has no `depends_on` edge:
  a dependency on `daily_run` would re-trigger it every day.
- `performance_check` (domain `performance`, stale after 48h), after
  `portfolio_snapshot`.

## Manual verification

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("SELECT asset_id, asset_type FROM assets").df())
    print(conn.execute("""
        SELECT severity, code, asset_id, currency, message
        FROM data_quality_issues ORDER BY created_at DESC LIMIT 20
    """).df())
```

Live check (2026-06-11): a portfolio of AGG + 005930.KS + BTC-USD + CASH_KRW +
CASH_USD snapshots at the correct USD total (KRW fetched on demand), AGG
classifies as `bond_etf`, and all seven assets in the registry refresh prices
and receive the six common factors daily — including the KR equity and crypto.
