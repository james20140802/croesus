# Transaction-Driven Snapshots and Performance Backfill

Sprint 009 closes the loop between the transaction ledger and portfolio state:
recording a trade is enough — no holdings CSV, and return numbers appear
immediately instead of after a month of daily snapshots.

## Snapshot without a CSV

`python -m croesus.jobs.portfolio_snapshot` (no `--holdings`) derives the
book from `portfolio_transactions` using the same average-cost fold the
performance engine uses, then marks to market as usual. `local_sync` does the
same when `CROESUS_HOLDINGS_PATH` is unset; with no transactions either, the
job skips gracefully with an actionable message.

Source rules:

- **CSV given** → the CSV is authoritative. If transactions are also
  recorded, the ledger is cross-checked: any security quantity differing by
  more than 0.5 % produces a `holdings reconciliation` warning naming the
  asset — a stale CSV or an unrecorded trade surfaces instead of drifting
  silently. (Cash rows are not compared; the two sources use different
  cash conventions.)
- **No CSV** → ledger-derived holdings, including `CASH_<CUR>` balances from
  deposits/withdrawals/dividends.
- **CSV configured but missing on disk** → the sync job skips loudly rather
  than silently switching to the ledger (a misconfiguration must not
  snapshot a different book).

## Performance backfill

```bash
python -m croesus.jobs.performance_backfill [--start YYYY-MM-DD] [--end YYYY-MM-DD]
```

Reconstructs one `portfolio_snapshots` row per trading day (dates present in
`prices_daily`; weekdays as a fallback for cash-only books) from the first
transaction date to today. Each day's holdings are derived from the ledger
and valued with that day's stored prices and FX, so the result is
deterministic and reproducible.

Guarantees:

- **Idempotent** — days that already have a snapshot row are skipped, so a
  second run writes nothing.
- **Non-destructive** — live snapshots are never overwritten; backfilled rows
  are marked `metadata.source = "performance_backfill"`.
- **Loud on gaps** — a day missing a price or FX rate still gets a snapshot,
  but ERROR issues land in `data_quality_issues` and the run reports the
  degraded-day count.

Only snapshot totals are reconstructed (what `performance_check` reads);
per-day holdings, exposures, and drifts are not.

## Typical first-run sequence

```bash
python -m croesus.jobs.record_transaction --type deposit --amount 20000 --date 2026-04-01
python -m croesus.jobs.record_transaction --type buy --asset AAPL --quantity 40 --price 205 --date 2026-04-02
python -m croesus.jobs.portfolio_snapshot          # ledger-derived, no CSV
python -m croesus.jobs.performance_backfill        # history appears
python -m croesus.jobs.performance_check           # 1m/3m returns now real
```
