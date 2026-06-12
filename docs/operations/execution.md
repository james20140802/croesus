# Post-Approval Execution — Paper Broker

Sprint 013 closes the roadmap: approved proposals can now be executed through
a broker adapter, paper-first. This is the **only** place in the codebase
that programmatically creates trade transactions.

## The gate, end to end

```
rebalance_check → proposal (pending, 7d expiry)
  → human: approve_action            (Sprint 011)
  → human: execute_approved          (this sprint — never scheduled)
      ✓ approved   ✓ window not lapsed   ✓ not already executed
  → BrokerAdapter.submit → Fill
  → portfolio_transactions row (linked_action_id = the action)
  → next ledger-derived snapshot reflects the trade (Sprint 009)
```

Blocked with a specific reason: pending ("approve it first"), rejected,
expired, approved-but-window-lapsed, already-executed (the ledger link makes
execution idempotent), unknown id. Sleeve-level proposals
(`rebalance_to_band` / `raise_cash` without an `asset_id`) are skipped — they
describe a target, not an order; break them into instrument trades manually.

## Usage

```bash
python -m croesus.jobs.list_pending_approvals          # find ids
python -m croesus.jobs.approve_action <id>             # decide
python -m croesus.jobs.execute_approved <id> --dry-run # see the order
python -m croesus.jobs.execute_approved <id>           # fill + ledger record
python -m croesus.jobs.execute_approved --all          # every executable one
```

## Paper broker semantics

Fills at the latest stored close on or before the execution date (the same
price surface every other module reads), zero slippage, flat `--fee`
(default 0). Deterministic; no network. A live adapter would implement the
same `BrokerAdapter` Protocol — the execution job owns all safety checks, so
no adapter can ever be reached by an unapproved action.

## Safety invariants (unchanged or enforced here)

- `execute_approved` is **not** registered in `local_sync`; a test asserts no
  scheduled job name contains execute/broker/order/submit.
- `BOUNDED_AUTO` trade mode is still rejected by profile validation.
- An approval that sat past its 7-day window is refused at execution time —
  the market context behind the proposal is stale; run a fresh
  `rebalance_check`.
