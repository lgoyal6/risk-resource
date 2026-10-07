# Evidence 0002: platform and benchmark slice

Date: 2026-10-07

- PostgreSQL migration defines tenant-scoped scenarios, idempotent job requests, recommendations, and append-only decision triggers.
- API-key authentication uses SHA-256 digests and role gates (`viewer`, `planner`, `approver`).
- Static UI is served at `/` and invokes the comparison endpoint.
- `python scripts/benchmark.py` on the fixed `demo-week` workload reported baseline objective 298443 cents, optimized objective 300943 cents, baseline 0.25 ms, optimized 3.55 ms in the verification run.
- `ruff`, `mypy`, and `pytest` remained green after this slice.
- `PostgresRepository.claim_job` uses `FOR UPDATE SKIP LOCKED` through a conditional update, and completion/failure are fenced by lease owner and running status. Live database execution remains environment-unverified.
- GitHub Actions workflow runs frozen dependency sync, Ruff, mypy, pytest, and the benchmark on pushes and pull requests.
