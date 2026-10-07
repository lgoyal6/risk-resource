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

Mandatory work orders must be scheduled, technicians need the skill and site clearance, capacity is measured at the selected P50 or P90 quantile, clusters have daily offline limits, and precedence windows are enforced. The model does not sequence work within a day, model parts inventory, or react to failures during the week. Persistence, worker leases, authentication, and a production UI are planned integration layers; the current local slice uses an injected in-process repository so it is easy to run and test.

Synthetic data generators and benchmarks must record their seed and provenance. Reports must distinguish the greedy baseline from CP-SAT and preserve the scenario hash for reproducibility.

## Durable deployment

`migrations/001_initial.sql` defines tenant-scoped scenarios, idempotent jobs, recommendations, and immutable decisions. `adapters.postgres.PostgresRepository` applies numbered migrations and preserves the raw scenario JSON and content hash. `adapters.worker.JobWorker` is the lease/fencing seam for a Postgres-backed worker. Run the local stack with `docker compose -f infra/docker-compose.yml up --build`.

Set `RR_API_KEYS` to a JSON map keyed by SHA-256 API-key digest, for example `{"<sha256>":{"user":"ops","team":"demo","role":"approver"}}`. Keys are never logged. `scripts/benchmark.py` prints a fixed seeded workload comparison with objective and elapsed time.

## PostgreSQL verification

With Docker available, run `scripts/verify_postgres.sh`. It starts the pinned PostgreSQL 16.4 service, waits for readiness, applies migrations, verifies scenario round-trip and tenant isolation, then removes the container and volume. Without `RR_POSTGRES_DSN`, the integration test is skipped rather than claiming coverage.

The full Compose smoke test builds the API image, waits for PostgreSQL health, runs startup migrations, and confirms `GET /health` before teardown.

### Security and worker verification

Protected API requests require `X-API-Key`, configured through a JSON map of token SHA-256 digests in `RR_API_KEYS`. Roles and team membership come from configuration. Approval actors come from the authenticated user, and `Idempotency-Key` binds retries to the exact decision and actor. Identical retries return 200; conflicting reuse returns 409. The browser demo accepts a key in memory rather than storing it in browser storage.

Run `bash scripts/verify_postgres.sh` to test actual worker claims, recommendation persistence, retry backoff, cancellation, expired-worker fencing, rollback, and append-only audit protection. The gate uses a uniquely owned database container with a temporary loopback port, creates isolated schemas per test, and cleans up on exit.

The reference HTTP API remains in memory. `DATABASE_URL` does not enable persistent HTTP approvals. PostgreSQL worker verification does not establish production deployment, availability, or a complete persistent API workflow.
