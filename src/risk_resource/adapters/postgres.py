from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from psycopg_pool import ConnectionPool

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.application.services import SolveService, scenario_hash
from risk_resource.domain.models import Scenario


class PostgresRepository:
    def __init__(self, dsn: str, service: SolveService | None = None):
        self.pool = ConnectionPool(dsn, open=False)
        self.service = service or SolveService(CpSatSolver())

    def open(self) -> None:
        self.pool.open()

    def close(self) -> None:
        self.pool.close()

    def migrate(self, directory: str = "migrations") -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version text primary key, applied_at timestamptz not null default now())"
            )
            for path in sorted(Path(directory).glob("*.sql")):
                version = path.name
                if conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=%s", (version,)
                ).fetchone():
                    continue
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES(%s)", (version,)
                )

    def save_scenario(self, scenario: Scenario, team: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO scenarios(id,team,name,provenance,raw_json,content_sha256) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    scenario.id,
                    team,
                    scenario.name,
                    scenario.provenance,
                    json.dumps(scenario.model_dump(mode="json")),
                    scenario_hash(scenario),
                ),
            )

    def get_scenario(self, scenario_id: str, team: str) -> Scenario | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT raw_json FROM scenarios WHERE id=%s AND team=%s",
                (scenario_id, team),
            ).fetchone()
        return Scenario.model_validate(row[0]) if row else None

    def claim_job(self, owner: str, lease_seconds: int):
        with self.pool.connection() as conn:
            row = conn.execute(
                """UPDATE jobs SET status='running', attempts=attempts+1, lease_owner=%s, lease_expires_at=now()+(%s * interval '1 second'), updated_at=now() WHERE id=(SELECT id FROM jobs WHERE (status='queued' OR (status='running' AND lease_expires_at < now())) AND cancel_requested=false AND attempts < max_attempts ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id, scenario_id, team""",
                (owner, lease_seconds),
            ).fetchone()
        return row

    def solve_job(self, job, owner: str):
        scenario = self.get_scenario(job[1], job[2])
        if scenario is None:
            raise RuntimeError("scenario disappeared")
        return self.service.optimized(scenario)

    def complete_job(self, job_id: UUID, owner: str, result) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE jobs SET status='succeeded', lease_owner=NULL, lease_expires_at=NULL, updated_at=now() WHERE id=%s AND lease_owner=%s AND status='running'",
                (job_id, owner),
            )

    def fail_job(self, job_id: UUID, owner: str, error: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE jobs SET status=CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END, error=%s, lease_owner=NULL, lease_expires_at=NULL, updated_at=now() WHERE id=%s AND lease_owner=%s AND status='running'",
                (error, job_id, owner),
            )
