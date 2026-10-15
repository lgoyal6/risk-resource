# 0008. Versioned scenarios, pinned solves and digest-bound approvals

Status: accepted (2026-10-15)

## Context

With `DATABASE_URL` set, the API already persists scenarios, solve jobs,
recommendations and the decision chain. Three gaps remained:

- A scenario could not change, so "approve a stale plan" had no meaning, and the
  worker solved whatever row it found at claim time rather than the input the
  planner asked about.
- `approve` accepted any feasible plan. Nothing tied the approved plan to the
  recommendation it named, so an edited plan could be recorded as an approval.
- The worker trusted the solver's own feasibility flag, and an infeasible
  scenario was retried three times before failing.

## Decision

**Aggregate root.** A scenario is `(team, id)` with immutable versions
`1..n` in `scenario_versions`. `scenarios` holds the head pointer
(`current_version`) and the head JSON for existing reads. A revision is
`POST /scenarios/{id}/versions` with `expected_version`; a mismatch is 409
(optimistic concurrency). Migration 004 backfills version 1 for existing rows.
A trigger refuses UPDATE and DELETE on `scenario_versions`.

**Pinned solve.** A solve job records `scenario_version`. The worker loads that
exact version, never the head. The job identity is
`sha256(scenario_id, version, content_sha256, Idempotency-Key or "default")`,
unique per team, so a repeated request with the same key returns the same job
and never enqueues a second solve. A new key requests a new solve of the same
version.

**Independent check at persistence.** `complete_job` re-runs the shared checker
against the pinned version. The solver's own evaluation is ignored. Outcomes:

| Outcome | Job status | Recommendation | Retry |
| --- | --- | --- | --- |
| checker accepts plan | `succeeded` | inserted with digest | no |
| solver proves infeasible, or returns a plan the checker rejects | `infeasible` / `failed` with diagnosis `InvalidSolution` | none | no, deterministic |
| solver raises (unavailable, crash) | `queued` then `failed` after max attempts | none | yes, backoff |
| worker dies after solving, before commit | lease expires, redelivered | exactly one (unique `job_id`) | yes |

Diagnoses store the checker's violation strings and the exception class name,
never exception text.

**Solution digest.** `sha256` of the canonical plan JSON with assignments sorted
by `(work_order_id, technician_id, day)`. Stored on the recommendation.

**Bound decisions.** A decision names `scenario_version` and, for approve and
override, carries the plan whose digest is recorded.

- `approve`: the plan digest must equal the recommendation digest, and the
  recommendation's version must equal both the request version and the current
  head. Otherwise 409 `stale_scenario` or 422 `digest_mismatch`.
- `override`: a reason is required, the edited plan must pass the checker
  against the current head, and its digest must differ from the
  recommendation's. The objective delta versus the recommendation is recorded.
  The override is a new decision row; the recommendation is never modified.
- `reject`: reason required, no plan.
- `rollback`: re-adopts an earlier approve/override plan; it must still pass
  the checker against the current head version.

The hash chain payload includes `scenario_version`, `solution_digest` and
`objective_delta_cents` only when present, so legacy entries (written before this
change) still verify.

**Ordering and concurrency.** The scenario row is locked `FOR UPDATE` for each
append, and the request's `previous_hash` must equal the stored tail. Two
concurrent overrides with different idempotency keys therefore produce one
201 and one 409; the client re-reads and retries. Same key, same payload
returns the original row (200); same key, different payload is 409.

**Cancellation and retention.** Unchanged from ADR 0003/0007. Versions, decisions
and recommendations are retained for the life of the scenario; there is no
delete path in the API.

**Compatibility.** The in-memory mode (no `DATABASE_URL`) keeps its existing
behaviour for local demos and does not claim durability. Decision requests
without `scenario_version` are accepted only in memory mode.

## Consequences

Approvals survive API restarts because every read goes to PostgreSQL. A
planner editing a scenario invalidates pending approvals of older
recommendations by construction, not by convention.
