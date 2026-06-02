# Sprint 006c: Transaction Ledger and Manual Execution Feedback

## Goal

Move Croesus from a pure snapshot calculator toward a portfolio operating
system that can remember how holdings changed over time.

```text
Proposed Actions
  -> User Approval / Manual Execution Record
  -> Transactions
  -> Derived Holdings
  -> Updated Snapshot
```

This sprint should follow the Level 1 proposal engine and freshness layer, and
it should come before approval-based execution. It does not require broker API
integration.

## Why This Exists

Sprint 004 and 004b use holdings snapshots as the source of truth. That is
acceptable for early MVP work, but it forces the user to keep replacing the
whole current portfolio state. A portfolio OS needs a ledger of changes:

- buys;
- sells;
- deposits;
- withdrawals;
- dividends;
- fees;
- manual execution of approved proposals.

Without a ledger, Croesus can diagnose the current portfolio, but it cannot
explain how the portfolio got there or close the loop after a proposal is
executed manually.

## Scope

### 1. Transaction Schema

Add a transaction ledger:

```sql
CREATE TABLE IF NOT EXISTS portfolio_transactions (
  transaction_id TEXT PRIMARY KEY,
  portfolio_id TEXT NOT NULL,
  asset_id TEXT,
  transaction_date DATE NOT NULL,
  transaction_type TEXT NOT NULL,
  quantity DOUBLE,
  price DOUBLE,
  gross_amount DOUBLE,
  currency TEXT,
  fees DOUBLE,
  source TEXT,
  linked_action_id TEXT,
  metadata JSON
);
```

Initial transaction types:

```text
buy
sell
deposit
withdrawal
dividend
fee
manual_adjustment
```

### 2. Manual Execution Feedback

Add a way to mark a proposed action as manually executed:

```bash
python -m croesus.jobs.record_execution --action-id ACTION --quantity 2 --price 190
```

Behavior:

1. Load the proposed action.
2. Validate that it belongs to the selected portfolio.
3. Record one or more transactions.
4. Link transactions back to the proposed action.
5. Leave broker execution out of scope.

### 3. Holdings Derivation

Add deterministic derivation from transactions:

```text
transactions
  -> position quantities
  -> average cost / cost basis
  -> portfolio_holdings rows
```

The first version may keep snapshot CSV import as an override path. The
transaction ledger should be additive, not a breaking replacement.

### 4. P&L Semantics

Define cost basis semantics clearly:

- `cost_basis` in holdings is base-currency total open cost.
- realized P&L comes from sell transactions.
- unrealized P&L comes from mark-to-market.
- dividends are income transactions, not negative cost basis.

Tax-lot accounting is out of scope; average cost is acceptable for the local MVP.

## Suggested Files

```text
croesus/portfolio/
  transactions.py
  transaction_repository.py
  holdings_from_transactions.py

croesus/jobs/
  record_execution.py
```

Tests:

```text
tests/test_transactions.py
tests/test_record_execution.py
```

## Acceptance Criteria

- Buy/sell/deposit/withdrawal/dividend/fee transactions can be stored.
- Holdings can be derived from transactions for a portfolio.
- Manual execution of a proposed action creates traceable transaction rows.
- Snapshot CSV import remains available for bootstrap and reconciliation.
- Realized and unrealized P&L semantics are documented and deterministic.
- No broker API calls are made.

## Out of Scope

- Tax-lot optimization.
- Wash-sale rules.
- Broker synchronization.
- Real order placement.
- Multi-currency tax reporting.
