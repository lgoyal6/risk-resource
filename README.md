# Risk Resource

Risk Resource is a decision-support system for preventive maintenance scheduling across a synthetic GPU server fleet. It allocates technician minutes and per-cluster drain capacity while making the baseline heuristic, optimized plan, objective terms, constraints, and approval evidence inspectable.

## Quickstart

```bash
uv sync --frozen
uv run pytest -q
uv run ruff check src tests
uv run mypy src
uv run uvicorn risk_resource.transport.api:app --reload
```

Then call `GET /scenarios`, `POST /scenarios/demo-week/baseline`, and `POST /scenarios/demo-week/solve`. The bundled scenario is labelled `demo`; it is not fleet telemetry.

## Architecture

`domain/` contains typed models, objective coefficients, feasibility checking, the greedy baseline, and hash-chained decisions. `application/` coordinates use cases. `adapters/` contains the CP-SAT solver port implementation. `transport/` exposes the public HTTP workflow. The solver uses one worker, a fixed seed, and a bounded time budget; an optimized plan is accepted only after the shared checker validates it.

## Constraints and limits

Mandatory work orders must be scheduled, technicians need the skill and site clearance, capacity is measured at the selected P50 or P90 quantile, clusters have daily offline limits, and precedence windows are enforced. The model does not sequence work within a day, model parts inventory, or react to failures during the week. Without `DATABASE_URL`, the local demo uses in-process scenarios and decisions. With `DATABASE_URL`, scenarios, versioned inputs, jobs, recommendations, and decisions use PostgreSQL.

Synthetic data generators and benchmarks must record their seed and provenance. Reports must distinguish the greedy baseline from CP-SAT and preserve the scenario hash for reproducibility.

## Durable deployment

`migrations/001_initial.sql` through `004_scenario_versions.sql` define tenant-scoped scenarios, version-pinned jobs, recommendations, and immutable decisions. `adapters.postgres.PostgresRepository` applies numbered migrations and preserves raw scenario JSON and content hashes. `adapters.worker.JobWorker` claims jobs with leases and fences stale workers. Run the local stack with `docker compose -f infra/docker-compose.yml up --build` after setting `RR_API_KEYS`.

Set `RR_API_KEYS` to a JSON map keyed by SHA-256 API-key digest, for example `{"<sha256>":{"user":"ops","team":"demo","role":"approver"}}`. Keys are never logged. `scripts/benchmark.py` prints a fixed seeded workload comparison with objective and elapsed time.

## PostgreSQL verification

With Docker available, run `bash scripts/verify_postgres.sh`. It starts a bounded PostgreSQL 16.4 container on a temporary loopback port, applies migrations, tests worker and HTTP persistence behavior, then removes the container. Without `RR_POSTGRES_DSN`, these integration tests are skipped rather than claiming coverage.

Run `uv run --frozen python scripts/verify_container.py` to build and exercise the API image against a bounded PostgreSQL container. The gate checks startup migrations, role and team boundaries, a durable solve and approval, database outage readiness, PostgreSQL restart recovery, and cleanup.

### Security and worker verification

Protected API requests require `X-API-Key`, configured through a JSON map of token SHA-256 digests in `RR_API_KEYS`. Roles and team membership come from configuration. Approval actors come from the authenticated user, and `Idempotency-Key` binds retries to the exact decision and actor. Identical retries return 200; conflicting reuse returns 409. The browser demo accepts a key in memory rather than storing it in browser storage.

Run `bash scripts/verify_postgres.sh` to test worker claims, recommendation persistence, retry backoff, cancellation, expired-worker fencing, crash redelivery, infeasible diagnosis, solver failure recovery, durable HTTP approvals, concurrent overrides, revision conflicts, and append-only audit protection. The gate creates isolated schemas per test and cleans up on exit. Local container verification does not establish production availability or operator adoption.


## Durable API mode

Set `DATABASE_URL` to enable PostgreSQL-backed scenarios, solve jobs, recommendations,
and append-only approval decisions. `POST /scenarios/{id}/solve` returns a job id. Run
`python -m risk_resource.transport.worker` as a separate process, or start the API,
worker, and PostgreSQL services with `docker compose -f infra/docker-compose.yml up --build`
after setting `RR_API_KEYS`. `--once` processes at most one job and exits. Scenario
revisions use `expected_version`; approvals and overrides must name the current version,
and approvals must match the persisted recommendation digest. Decision history and hash
verification read PostgreSQL after API restarts. Job cancellation is team-scoped and
fenced against in-flight workers. The API process closes its connection pool at shutdown.
