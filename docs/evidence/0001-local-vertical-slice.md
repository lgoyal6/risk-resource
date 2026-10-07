# Evidence 0001: local vertical slice

Date: 2026-10-07

- `uv run pytest -q`: 4 tests passed, proving deterministic greedy output, shared feasibility checks, duplicate assignment rejection, CP-SAT feasibility, and the HTTP workflow.
- `uv run ruff check src tests`: passed.
- `uv run mypy src`: passed with no issues.
- The demo scenario is explicitly labelled `demo` and uses no external telemetry.
