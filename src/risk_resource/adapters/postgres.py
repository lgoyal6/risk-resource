from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.adapters.worker import JobLease
from risk_resource.application.services import SolveService, scenario_hash
from risk_resource.domain.decisions import DecisionEntry
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

    def list_scenarios(self, team: str) -> list[Scenario]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT raw_json FROM scenarios WHERE team=%s ORDER BY created_at,id", (team,)).fetchall()
        return [Scenario.model_validate(row[0]) for row in rows]

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

    def recommendation(self, recommendation_id: str, scenario_id: str, team: str):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT r.id,r.job_id,r.scenario_id,r.plan_json,r.evaluation_json FROM recommendations r JOIN scenarios s ON s.id=r.scenario_id WHERE r.id=%s AND r.scenario_id=%s AND s.team=%s", (recommendation_id, scenario_id, team)).fetchone()
        if row is None:
            return None
        return {"id": str(row[0]), "job_id": str(row[1]), "scenario_id": row[2], "plan": row[3], "evaluation": row[4]}

    def recommendation_for_job(self, job_id: str, scenario_id: str, team: str):
        """Return the durable result attached to a job, scoped to its team."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT r.id,r.job_id,r.scenario_id,r.plan_json,r.evaluation_json "
                "FROM recommendations r JOIN scenarios s ON s.id=r.scenario_id "
                "WHERE r.job_id=%s AND r.scenario_id=%s AND s.team=%s",
                (job_id, scenario_id, team),
            ).fetchone()
        if row is None:
            return None
        return {"id": str(row[0]), "job_id": str(row[1]), "scenario_id": row[2], "plan": row[3], "evaluation": row[4]}

    def job(self, job_id: str, scenario_id: str, team: str):
        with self.pool.connection() as conn:
            row = conn.execute("SELECT id,scenario_id,status,attempts,error FROM jobs WHERE id=%s AND scenario_id=%s AND team=%s", (job_id, scenario_id, team)).fetchone()
        return {"id": str(row[0]), "scenario_id": row[1], "status": row[2], "attempts": row[3], "error": row[4]} if row else None

    def decisions(self, scenario_id: str, team: str) -> list[DecisionEntry]:
        from risk_resource.domain.decisions import DecisionEntry
        from risk_resource.domain.models import Plan
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT d.action,d.recommendation_id,d.actor,d.reason,d.plan_json,d.previous_hash,d.hash FROM decisions d JOIN scenarios s ON s.id=d.scenario_id WHERE d.scenario_id=%s AND s.team=%s ORDER BY d.id", (scenario_id, team)).fetchall()
        return [DecisionEntry(r[0], str(r[1]) if r[1] else "", r[2], r[3], Plan.model_validate(r[4]) if r[4] else None, r[5], r[6]) for r in rows]

    def append_decision(
        self,
        scenario: Scenario,
        team: str,
        entry: DecisionEntry,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[DecisionEntry, bool]:
        with self.pool.connection() as conn:
            # The row lock makes previous_hash and the append-only hash chain linear.
            conn.execute("SELECT id FROM scenarios WHERE id=%s AND team=%s FOR UPDATE", (scenario.id, team))
            if idempotency_key is not None:
                if not request_hash:
                    raise ValueError("request hash is required with an idempotency key")
                prior = conn.execute(
                    "SELECT request_hash,decision_hash FROM decision_idempotency WHERE team=%s AND scenario_id=%s AND idempotency_key=%s",
                    (team, scenario.id, idempotency_key),
                ).fetchone()
                if prior:
                    if prior[0] != request_hash:
                        raise ValueError("idempotency key reused for a different decision")
                    row = conn.execute(
                        "SELECT action,recommendation_id,actor,reason,plan_json,previous_hash,hash FROM decisions WHERE scenario_id=%s AND hash=%s",
                        (scenario.id, prior[1]),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError("decision idempotency record has no decision")
                    return self._decision_entry(row), False
            prior = conn.execute("SELECT hash FROM decisions WHERE scenario_id=%s ORDER BY id DESC LIMIT 1", (scenario.id,)).fetchone()
            if (prior[0] if prior else "") != entry.previous_hash:
                raise ValueError("decision chain changed; retry the command")
            rec = None
            try:
                rec = UUID(entry.recommendation_id)
            except ValueError:
                if entry.action != "reject":
                    raise ValueError("recommendation_id must reference a persisted recommendation")
            if rec and self.recommendation(str(rec), scenario.id, team) is None:
                raise ValueError("recommendation does not belong to this scenario")
            conn.execute("INSERT INTO decisions(scenario_id,action,recommendation_id,actor,reason,plan_json,previous_hash,hash) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", (scenario.id,entry.action,rec,entry.actor,entry.reason,Jsonb(entry.plan.model_dump(mode="json")) if entry.plan else None,entry.previous_hash,entry.hash))
            if idempotency_key is not None:
                conn.execute(
                    "INSERT INTO decision_idempotency(team,scenario_id,idempotency_key,request_hash,decision_hash) VALUES(%s,%s,%s,%s,%s)",
                    (team, scenario.id, idempotency_key, request_hash, entry.hash),
                )
            return entry, True

    @staticmethod
    def _decision_entry(row) -> DecisionEntry:
        from risk_resource.domain.models import Plan

        recommendation_id = str(row[1]) if row[1] else ""
        return DecisionEntry(
            row[0], recommendation_id, row[2], row[3],
            Plan.model_validate(row[4]) if row[4] else None,
            row[5], row[6],
        )
