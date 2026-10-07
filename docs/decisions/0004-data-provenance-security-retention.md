# ADR 0004: Synthetic data labelling, API-key auth, tenant isolation, retention

Status: accepted (2026-10-06)

## Data provenance

Every scenario has `provenance` in `{demo, synthetic_benchmark, pilot, real}`. Bundled
fixtures and generated workloads are `demo` or `synthetic_benchmark`. The UI shows the
label on every scenario; benchmark output names the generator and seed. No number in the
report is presented as fleet telemetry.

The raw uploaded scenario JSON is stored verbatim next to the parsed form along with its
SHA-256, so what the solver saw can always be compared to what the user sent.

## Authentication and authorization

- API keys, configured as `RR_API_KEYS` (JSON map of `sha256(key)` to
  `{user, team, role}`). Only hashes are configured, compared in constant time; keys are
  never logged or stored.
- Roles: `viewer` (read), `planner` (+ create scenarios, copy, solve, cancel),
  `approver` (+ decision log writes).
- **Tenant isolation**: every scenario, job and recommendation belongs to a team. Every
  query filters by the caller's team, and cross-team reads return 404 (not 403), so IDs
  of another team's objects are not confirmed to exist.

## Unsafe input

- Request bodies over 2 MiB are rejected before parsing.
- Scenario limits: 2,000 work orders, 200 technicians, 14-day horizon. These keep a solve
  inside the worker's memory budget; raising them is a deliberate change.
- Identifiers match `^[A-Za-z0-9._-]{1,64}$`. All SQL is parameterized. The UI renders
  user strings with `textContent`, never `innerHTML`.

## Retention

- Scenarios, recommendations and decisions are retained indefinitely: they are the
  audit record of why maintenance was or was not done.
- Job rows older than 30 days in a terminal state may be deleted
  (`risk-resource prune-jobs --days 30`); the recommendation they produced is kept.
- No personal data beyond the configured user names appears in records.
