# Transaction Ledger and Manual Execution Feedback

Sprint 006c moves Croesus from a snapshot calculator toward a portfolio
operating system that remembers *how* holdings changed. Instead of replacing the
whole holdings CSV every time the portfolio moves, you record ordinary events —
buys, sells, deposits, withdrawals, dividends, fees — and holdings are derived
from them deterministically.

```text
Proposed Actions
  -> Manual Execution Record (record_execution)
  -> Transactions (portfolio_transactions)
  -> Derived Holdings (holdings_from_transactions)
  -> Mark-to-market / Snapshot
```

The ledger is **additive**: snapshot CSV import (`portfolio_snapshot`) still
works for bootstrap and reconciliation. Nothing here calls a broker or places an
order — `record_execution` only records a fill the user already made manually.

## Recording the manual execution of a proposed action

```bash
python -m croesus.jobs.record_execution --action-id ACTION --quantity 2 --price 190
```

It loads the proposed action, finds the portfolio that owns it (via the
rebalance run), infers buy vs. sell from the action type, writes one transaction,
and links it back with `linked_action_id`.

| Flag | Effect |
|---|---|
| `--action-id ID` | Proposed action that was filled (required). |
| `--quantity Q` / `--price P` | Filled quantity and price per share (required). |
| `--type buy\|sell` | Override the inferred direction. |
| `--fees F` | Fees paid on the fill. |
| `--currency CUR` | Trade currency (defaults to the portfolio base). |
| `--portfolio-id ID` | Assert the action belongs to this portfolio before writing. |
| `--date YYYY-MM-DD` | Execution date (default: today). |

Direction inference: `trim` / `raise_cash` → sell, `add` → buy,
`rebalance_to_band` → sell when the proposed weight is below current, else buy.
`hold` / `watch` and other non-trade actions require an explicit `--type`.

A lower-level path records a transaction with no linked action:

```python
from croesus.portfolio.transaction_repository import TransactionRepository
TransactionRepository(conn).record_transaction(txn)  # -> TransactionResult
```

Both paths **validate before writing** and return a structured result
(`recorded` / `rejected` + field errors) suitable for a future form flow.

## `portfolio_transactions`

One append-only row per event: `transaction_id`, `portfolio_id`, `asset_id`,
`transaction_date`, `transaction_type`, `quantity`, `price`, `gross_amount`,
`currency`, `fees`, `source`, `linked_action_id`, `metadata`.

Transaction types are a stable product contract (a future UI uses them in forms
and filters): `buy`, `sell`, `deposit`, `withdrawal`, `dividend`, `fee`,
`manual_adjustment`.

## Deriving holdings

`derive_holdings_from_transactions(...)` folds a portfolio's transactions, oldest
first, into the same `Holding` rows the snapshot pipeline persists, plus a cash
balance per currency and realized P&L. Positions that net to zero are dropped.

## P&L semantics (average cost; tax lots out of scope)

- **cost basis** is the base-currency total *open* cost of a position, including
  buy-side fees.
- **realized P&L** comes from `sell` transactions: `(price − avg_cost) × qty −
  fees`, where `avg_cost` is the running average at the time of sale.
- **unrealized P&L** is **not** computed here — it comes from mark-to-market
  against live prices, exactly as the snapshot pipeline already does.
- **dividends** are income: they add cash and do **not** reduce cost basis.
- **fees** reduce cash; they are capitalized into a position's cost basis only
  when the fee names an `asset_id`.
- a **sell larger than the held quantity** is clamped to the position and warned
  (long-only MVP — no short positions).

## Out of scope

Tax-lot optimization, wash-sale rules, broker synchronization, real order
placement, and multi-currency tax reporting.
