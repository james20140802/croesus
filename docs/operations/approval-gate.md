# Approval Gate — Human Decision Records on Trade Proposals

Sprint 011 gives every trade proposal an explicit approval lifecycle. Nothing
in the codebase executes a trade; the gate exists so that when execution
arrives (Sprint 013, paper broker first), it can act **only** on actions a
human approved while the proposal was still fresh.

## Lifecycle

```
persist (requires_user_approval) ──► pending ──┬─► approved   (human, once)
                                               ├─► rejected   (human, once)
                                               └─► expired    (automatic, 7 days)
```

- The repository stamps `pending` + `expires_at = now + 7d` whenever an
  approvable action is persisted — no caller can create an approvable action
  without an approval record.
- Expiry is swept deterministically before every read or decision, so a stale
  proposal can never be approved, even if no job ran in between.
- A decided or expired action cannot be re-decided; the error tells you to run
  a fresh `rebalance_check` and decide on the new proposal.
- A new rebalance run writes new rows under a new `run_id` — earlier
  decisions are never overwritten (verified live: approved/rejected records
  survive re-runs intact).

## CLI

```bash
python -m croesus.jobs.list_pending_approvals
python -m croesus.jobs.approve_action <action_id> [--notes "..."]
python -m croesus.jobs.approve_action <action_id> --reject [--notes "..."]
```

The portfolio action report shows each proposal's approval status and expiry
(`— approval: pending (id ..., expires 2026-06-19)`), and the CSV gains
`approval_status` / `expires_at` columns.

## Why expiry exists

A proposal is computed from one day's prices, exposures, and macro state.
Approving it weeks later would mean acting on stale context; the 7-day window
(`APPROVAL_TTL_DAYS` in `croesus/portfolio/actions.py`) forces a fresh run
instead.

## Manual verification

```python
from croesus.db.connection import get_connection

with get_connection() as conn:
    print(conn.execute("""
        SELECT action_id, action_type, asset_id, approval_status,
               approved_at, expires_at
        FROM proposed_actions
        WHERE approval_status IS NOT NULL
        ORDER BY expires_at DESC LIMIT 10
    """).df())
```
