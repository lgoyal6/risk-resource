# ADR 0005: deliver a local vertical slice before external infrastructure

Status: accepted (2026-10-07)

The first reviewable increment keeps repository and job storage in process while preserving ports for later PostgreSQL and worker adapters. This makes the domain, baseline, solver, API contract, and approval invariants executable in a clean local checkout. External persistence and asynchronous worker deployment remain integration work, and the README labels that boundary explicitly.
