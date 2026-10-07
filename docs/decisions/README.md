# Decisions

Architecture decision records. Each one states the assumption before the code relies on it.

| ADR | Decision |
| --- | --- |
| [0001](0001-domain-gpu-fleet-maintenance.md) | Domain: preventive maintenance scheduling for a GPU server fleet |
| [0002](0002-architecture-and-solver.md) | Python, CP-SAT behind a solver port, PostgreSQL as the source of truth |
| [0003](0003-async-jobs-and-approvals.md) | Postgres-backed job queue with leases; append-only, hash-chained decision log |
| [0004](0004-data-provenance-security-retention.md) | Synthetic data labelling, API-key auth, tenant isolation, retention |
