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
