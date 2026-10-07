# ADR 0002: Python, CP-SAT behind a solver port, PostgreSQL as the source of truth

Status: accepted (2026-10-06)

## Decision

- **Language: Python 3.13**, dependencies locked in `uv.lock`, installed with
  `uv sync --frozen`. The deciding factor is the solver ecosystem: OR-Tools CP-SAT ships
  first-class Python wheels; Go has no maintained CP-SAT binding.
- **Layering** (`src/risk_resource/`):
  - `domain/` pure rules: model types, objective table, plan checker, feasibility
    validation, greedy baseline, uncertainty evaluation, decision-log state machine.
    No I/O, no OR-Tools import.
  - `application/` use cases (import, solve, compare, sensitivity, approve, export) and
    the ports they depend on (`Solver`, `Repository`).
  - `adapters/` CP-SAT solver, PostgreSQL repository.
  - `transport/` HTTP API (FastAPI), CLI, worker loop, static UI.
  - Wiring happens in one place per process (`transport/wiring.py`); no module-level
    mutable state, metrics registries are constructed and injected.
- **Solver port.** `Solver.solve(scenario, params, should_stop) -> SolveResult`. Two
  implementations: `GreedySolver` (domain, transparent baseline) and `CpSatSolver`
  (adapter). Both return plans that the same domain checker validates; a solver result
  that fails the checker is an error, never a recommendation.
- **Determinism.** CP-SAT runs with `num_workers=1`, a fixed `random_seed`, and a
  `max_deterministic_time` budget, so the same input and parameters produce the same
  plan on the same OR-Tools version. A wall-clock `max_time_in_seconds` is a separate
  safety timeout; when it fires before the deterministic budget the result is marked
  `timed_out=true` and is not claimed to be reproducible.
- **Warm start.** The greedy plan is passed to CP-SAT as a solution hint when it is
  feasible, so the optimizer starts from the baseline. This is stated in the report:
  "optimized >= baseline" is partly by construction, the size of the gap is not.
- **PostgreSQL 16** is the source of truth for scenarios, jobs, recommendations and
  decisions. Migrations are plain numbered SQL files applied by `risk-resource migrate`,
  tracked in `schema_migrations`.

## Rejected

- A MILP via HiGHS: workable, but CP-SAT's assumption-based infeasibility cores give the
  "which mandatory orders conflict" explanation directly.
- An in-memory repository for faster unit tests: the services are tested against real
  PostgreSQL instead, which is what the workflow requires anyway.
