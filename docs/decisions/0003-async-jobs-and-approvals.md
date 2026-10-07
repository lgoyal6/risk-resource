# ADR 0003: Postgres-backed job queue with leases; append-only, hash-chained decision log

Status: accepted (2026-10-06)

## Async solving

Solves and sensitivity sweeps run in a separate worker process. The queue is the `jobs`
table; no broker is added because Postgres already has the durability guarantees and one
fewer service is one fewer failure mode.

State machine:

```
queued --claim--> running --complete--> succeeded
   |                 |  \--error, attempts < max--> queued (retry)
   |                 |  \--error, attempts = max--> failed
   |                 \--lease expired (worker died)--> reclaimable as running by another worker
   \--cancel--> cancelled          running --cancel requested, worker observes--> cancelled
```

- **Claim** uses `FOR UPDATE SKIP LOCKED`, increments `attempts`, sets `lease_owner` and
  `lease_expires_at` from the database clock (worker clocks are never trusted).
- **Heartbeat** extends the lease while the solver runs.
- **Fencing.** Completion updates are conditional on `lease_owner = me AND status =
  'running'`. A worker whose lease was stolen cannot overwrite the new owner's result.
- **Exactly-once result.** `recommendations.job_id` is unique; a retried job that already
  wrote its recommendation before crashing finds it and completes without a duplicate.
- **Idempotency.** `POST /solves` takes an `Idempotency-Key`. Same key and same request
  body returns the original job; same key with a different body is a 409. The request
  body hash is stored to detect that.
- **Cancellation.** A queued job is cancelled immediately. A running job gets
  `cancel_requested = true`; the worker's heartbeat thread sees it and calls CP-SAT's
  `stop_search`, then records `cancelled`. Cancelling a finished job is a no-op that
  returns the final state.
- **Poison jobs** stop after `max_attempts` (3) and are marked `failed` with the last
  error preserved.

## Approvals

Each scenario has a decision log. Entries are append-only and hash-chained:

```
hash_n = sha256(hash_{n-1} || canonical_json(entry_n without hash))
```

Actions:

- `approve(recommendation)` - adopt the solver plan as-is.
- `override(recommendation, plan, reason)` - adopt a human-edited plan. The edited plan
  must pass the hard-constraint checker; a reason is mandatory; the objective delta vs
  the recommendation is recorded so the cost of the override is visible.
- `reject(recommendation, reason)`.
- `rollback(to_entry, reason)` - re-adopt the plan of an earlier approve/override entry.

The "active plan" of a scenario is derived from the log, never stored separately, so it
cannot drift from history. A database trigger refuses `UPDATE` and `DELETE` on
`decisions`; `GET /scenarios/{id}/decisions/verify` recomputes the chain and reports
the first broken link. The trigger stops accidents and the application path; the chain
detects edits made by someone with enough privilege to drop the trigger.

Only the `approver` role may write decisions. Recommendations from a different
scenario cannot be approved under this one (scenario isolation).
