from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from psycopg_pool import ConnectionPool

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.adapters.worker import JobLease
from risk_resource.application.services import SolveService, scenario_hash
from risk_resource.domain.models import Scenario, SolveResult


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

    def enqueue_job(
        self, scenario_id: str, team: str, request_hash: str, max_attempts: int = 3
    ) -> UUID:
        if max_attempts < 1 or not request_hash:
            raise ValueError("request hash and positive attempt budget are required")
        if self.get_scenario(scenario_id, team) is None:
            raise ValueError("scenario not found for team")
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO jobs(id,scenario_id,team,status,request_hash,max_attempts) VALUES(%s,%s,%s,'queued',%s,%s) ON CONFLICT(team,request_hash) DO NOTHING",
                (uuid4(), scenario_id, team, request_hash, max_attempts),
            )
            row = conn.execute(
                "SELECT id, scenario_id FROM jobs WHERE team=%s AND request_hash=%s",
                (team, request_hash),
            ).fetchone()
            if row is None or row[1] != scenario_id:
                raise ValueError("request hash reused for another scenario")
            return row[0]

    def claim_job(self, owner: str, lease_seconds: int) -> JobLease | None:
        if not owner.strip() or lease_seconds < 1:
            raise ValueError("worker owner and positive lease duration are required")
        with self.pool.connection() as conn:
            # A crash on the final attempt must become terminal, not remain RUNNING forever.
            conn.execute(
                "UPDATE jobs SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END, lease_owner=NULL, lease_expires_at=NULL, updated_at=now() WHERE (status='queued' OR (status='running' AND lease_expires_at <= now())) AND (cancel_requested OR attempts >= max_attempts)"
            )
            row = conn.execute(
                """UPDATE jobs j SET status='running', attempts=j.attempts+1,
                   lease_owner=%s, lease_expires_at=now()+(%s * interval '1 second'), updated_at=now()
                   WHERE j.id=(SELECT id FROM jobs WHERE
                       ((status='queued' AND (next_retry_at IS NULL OR next_retry_at <= now()))
                        OR (status='running' AND lease_expires_at <= now()))
                       AND cancel_requested=false AND attempts < max_attempts
                       ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING id,scenario_id,team,attempts""",
                (owner, lease_seconds),
            ).fetchone()
        return JobLease(row[0], row[1], row[2], owner, row[3]) if row else None

    def solve_job(self, job: JobLease) -> SolveResult:
        scenario = self.get_scenario(job.scenario_id, job.team)
        if scenario is None:
            raise RuntimeError("scenario disappeared")
        return self.service.optimized(scenario)

    @staticmethod
    def _fence() -> str:
        return "id=%s AND team=%s AND lease_owner=%s AND attempts=%s AND status='running' AND cancel_requested=false AND lease_expires_at > now()"

    @staticmethod
    def _lease_args(job: JobLease) -> tuple:
        return job.id, job.team, job.owner, job.attempt

    def heartbeat(self, job: JobLease, lease_seconds: int) -> bool:
        if lease_seconds < 1:
            raise ValueError("positive lease duration required")
        with self.pool.connection() as conn:
            return (
                conn.execute(
                    "UPDATE jobs SET lease_expires_at=now()+(%s * interval '1 second') WHERE "
                    + self._fence(),
                    (lease_seconds, *self._lease_args(job)),
                ).rowcount
                == 1
            )

    def complete_job(self, job: JobLease, result: SolveResult) -> bool:
        if not result.evaluation.feasible:
            raise ValueError("infeasible recommendations cannot be completed")
        with self.pool.connection() as conn:
            updated = conn.execute(
                "UPDATE jobs SET status='succeeded',error=NULL,next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE "
                + self._fence(),
                self._lease_args(job),
            )
            if updated.rowcount != 1:
                return False
            # Result persistence and success share one transaction. An insert failure rolls both back.
            conn.execute(
                "INSERT INTO recommendations(id,job_id,scenario_id,plan_json,evaluation_json) VALUES(%s,%s,%s,%s,%s)",
                (
                    uuid4(),
                    job.id,
                    job.scenario_id,
                    result.plan.model_dump_json(),
                    result.evaluation.model_dump_json(),
                ),
            )
            return True

    def fail_job(self, job: JobLease, error: str) -> bool:
        delay = min(300, 2 ** min(8, job.attempt - 1))
        with self.pool.connection() as conn:
            return (
                conn.execute(
                    "UPDATE jobs SET status=CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,error=%s,next_retry_at=now()+(%s * interval '1 second'),lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE "
                    + self._fence(),
                    (error, delay, *self._lease_args(job)),
                ).rowcount
                == 1
            )

    def cancel_job(self, job_id: UUID, team: str) -> bool:
        with self.pool.connection() as conn:
            return (
                conn.execute(
                    "UPDATE jobs SET status='cancelled',cancel_requested=true,lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE id=%s AND team=%s AND status IN ('queued','running')",
                    (job_id, team),
                ).rowcount
                == 1
            )
