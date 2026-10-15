"""Persist versioned scenarios, leased solve jobs and bound approval decisions.
Used by the HTTP API and JobWorker; migrations define the durable constraints.
Decision validation uses the transaction's connection to avoid pool starvation.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.adapters.worker import JobLease
from risk_resource.application.approvals import (
    Recommendation,
    StaleScenario,
    plan_digest,
)
from risk_resource.application.services import SolveService, scenario_hash
from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.decisions import DecisionEntry
from risk_resource.domain.models import Plan, Scenario, SolveResult

_DECISION_COLUMNS = "action,recommendation_id,actor,reason,plan_json,previous_hash,hash,scenario_version,solution_digest,objective_delta_cents"


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
                "CREATE TABLE IF NOT EXISTS schema_migrations(version text primary key, applied_at "
                "timestamptz not null default now())"
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

    def save_scenario(
        self, scenario: Scenario, team: str, actor: str | None = None
    ) -> None:
        raw = json.dumps(scenario.model_dump(mode="json"))
        with self.pool.connection() as conn:
            conn.execute(
                (
                    "INSERT INTO "
                    "scenarios(id,team,name,provenance,raw_json,content_sha256,current_version) "
                    "VALUES(%s,%s,%s,%s,%s,%s,1)"
                ),
                (
                    scenario.id,
                    team,
                    scenario.name,
                    scenario.provenance,
                    raw,
                    scenario_hash(scenario),
                ),
            )
            conn.execute(
                (
                    "INSERT INTO "
                    "scenario_versions(scenario_id,version,raw_json,content_sha256,created_by) "
                    "VALUES(%s,1,%s,%s,%s)"
                ),
                (scenario.id, raw, scenario_hash(scenario), actor),
            )

    def head(self, scenario_id: str, team: str) -> tuple[Scenario, int] | None:
        """Current scenario and its version, team-scoped."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT raw_json,current_version FROM scenarios WHERE id=%s AND team=%s",
                (scenario_id, team),
            ).fetchone()
        return (Scenario.model_validate(row[0]), row[1]) if row else None

    def scenario_version(
        self, scenario_id: str, team: str, version: int
    ) -> Scenario | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT v.raw_json FROM scenario_versions v JOIN scenarios s ON s.id=v.scenario_id "
                "WHERE v.scenario_id=%s AND s.team=%s AND v.version=%s",
                (scenario_id, team, version),
            ).fetchone()
        return Scenario.model_validate(row[0]) if row else None

    def revise_scenario(
        self, scenario: Scenario, team: str, expected_version: int, actor: str
    ) -> int:
        """Append an immutable version; optimistic on expected_version."""
        raw = json.dumps(scenario.model_dump(mode="json"))
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT current_version FROM scenarios WHERE id=%s AND team=%s FOR UPDATE",
                (scenario.id, team),
            ).fetchone()
            if row is None:
                raise LookupError("scenario not found for team")
            if row[0] != expected_version:
                raise StaleScenario(
                    f"scenario is at version {row[0]}, expected {expected_version}"
                )
            version = row[0] + 1
            conn.execute(
                (
                    "INSERT INTO "
                    "scenario_versions(scenario_id,version,raw_json,content_sha256,created_by) "
                    "VALUES(%s,%s,%s,%s,%s)"
                ),
                (scenario.id, version, raw, scenario_hash(scenario), actor),
            )
            conn.execute(
                (
                    "UPDATE scenarios SET "
                    "raw_json=%s,content_sha256=%s,name=%s,provenance=%s,current_version=%s WHERE id=%s "
                    "AND team=%s"
                ),
                (
                    raw,
                    scenario_hash(scenario),
                    scenario.name,
                    scenario.provenance,
                    version,
                    scenario.id,
                    team,
                ),
            )
            return version

    def get_scenario(self, scenario_id: str, team: str) -> Scenario | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT raw_json FROM scenarios WHERE id=%s AND team=%s",
                (scenario_id, team),
            ).fetchone()
        return Scenario.model_validate(row[0]) if row else None

    def list_scenarios(self, team: str) -> list[Scenario]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT raw_json FROM scenarios WHERE team=%s ORDER BY created_at,id",
                (team,),
            ).fetchall()
        return [Scenario.model_validate(row[0]) for row in rows]

    def enqueue_job(
        self,
        scenario_id: str,
        team: str,
        request_hash: str,
        max_attempts: int = 3,
        scenario_version: int | None = None,
    ) -> UUID:
        """Queue one solve of a pinned version; the request hash is the idempotency key."""
        if max_attempts < 1 or not request_hash:
            raise ValueError("request hash and positive attempt budget are required")
        current = self.head(scenario_id, team)
        if current is None:
            raise ValueError("scenario not found for team")
        version = current[1] if scenario_version is None else scenario_version
        if self.scenario_version(scenario_id, team, version) is None:
            raise ValueError("scenario version not found for team")
        with self.pool.connection() as conn:
            conn.execute(
                (
                    "INSERT INTO "
                    "jobs(id,scenario_id,team,status,request_hash,max_attempts,scenario_version) "
                    "VALUES(%s,%s,%s,'queued',%s,%s,%s) ON CONFLICT(team,request_hash) DO NOTHING"
                ),
                (uuid4(), scenario_id, team, request_hash, max_attempts, version),
            )
            row = conn.execute(
                "SELECT id, scenario_id, scenario_version FROM jobs WHERE team=%s AND request_hash=%s",
                (team, request_hash),
            ).fetchone()
            if row is None or row[1] != scenario_id or row[2] != version:
                raise ValueError("request hash reused for another scenario or version")
            return row[0]

    def claim_job(self, owner: str, lease_seconds: int) -> JobLease | None:
        if not owner.strip() or lease_seconds < 1:
            raise ValueError("worker owner and positive lease duration are required")
        with self.pool.connection() as conn:
            # A crash on the final attempt must become terminal, not remain RUNNING forever.
            conn.execute(
                "UPDATE jobs SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' "
                "END, lease_owner=NULL, lease_expires_at=NULL, updated_at=now() WHERE "
                "(status='queued' OR (status='running' AND lease_expires_at <= now())) AND "
                "(cancel_requested OR attempts >= max_attempts)"
            )
            row = conn.execute(
                """UPDATE jobs j SET status='running', attempts=j.attempts+1,
                   lease_owner=%s, lease_expires_at=now()+(%s * interval '1 second'), updated_at=now()
                   WHERE j.id=(SELECT id FROM jobs WHERE
                       ((status='queued' AND (next_retry_at IS NULL OR next_retry_at <= now()))
                        OR (status='running' AND lease_expires_at <= now()))
                       AND cancel_requested=false AND attempts < max_attempts
                       ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING id,scenario_id,team,attempts,scenario_version""",
                (owner, lease_seconds),
            ).fetchone()
        return JobLease(row[0], row[1], row[2], owner, row[3], row[4]) if row else None

    def solve_job(self, job: JobLease) -> SolveResult:
        # Solve the version the request pinned, never whatever the head is now.
        scenario = self.scenario_version(
            job.scenario_id, job.team, job.scenario_version
        )
        if scenario is None:
            raise RuntimeError("scenario version disappeared")
        return self.service.optimized(scenario)

    @staticmethod
    def _fence() -> str:
        return (
            "id=%s AND team=%s AND lease_owner=%s AND attempts=%s AND status='running' AND "
            "cancel_requested=false AND lease_expires_at > now()"
        )

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
        """Commit the lease's outcome. The checker, not the solver, decides feasibility."""
        scenario = self.scenario_version(
            job.scenario_id, job.team, job.scenario_version
        )
        if scenario is None:
            raise RuntimeError("scenario version disappeared")
        evaluation = evaluate_plan(scenario, result.plan)
        with self.pool.connection() as conn:
            if not evaluation.feasible:
                # Deterministic outcome: record a terminal diagnosis, never a recommendation.
                kind = "Infeasible" if result.proven_infeasible else "InvalidSolution"
                status = "infeasible" if result.proven_infeasible else "failed"
                return (
                    conn.execute(
                        (
                            "UPDATE jobs SET "
                            "status=%s,error=%s,diagnosis=%s,next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=now()"
                            " WHERE "
                        )
                        + self._fence(),
                        (
                            status,
                            kind,
                            Jsonb(
                                {
                                    "kind": kind,
                                    "violations": list(evaluation.violations),
                                }
                            ),
                            *self._lease_args(job),
                        ),
                    ).rowcount
                    == 1
                )
            updated = conn.execute(
                (
                    "UPDATE jobs SET "
                    "status='succeeded',error=NULL,next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=now()"
                    " WHERE "
                )
                + self._fence(),
                self._lease_args(job),
            )
            if updated.rowcount != 1:
                return False
            # Result persistence and success share one transaction. An insert failure rolls both back.
            conn.execute(
                (
                    "INSERT INTO "
                    "recommendations(id,job_id,scenario_id,plan_json,evaluation_json,scenario_version,solution_digest)"
                    " VALUES(%s,%s,%s,%s,%s,%s,%s)"
                ),
                (
                    uuid4(),
                    job.id,
                    job.scenario_id,
                    result.plan.model_dump_json(),
                    evaluation.model_dump_json(),
                    job.scenario_version,
                    plan_digest(result.plan),
                ),
            )
            return True

    def fail_job(self, job: JobLease, error: str) -> bool:
        delay = min(300, 2 ** min(8, job.attempt - 1))
        with self.pool.connection() as conn:
            return (
                conn.execute(
                    (
                        "UPDATE jobs SET status=CASE WHEN attempts >= max_attempts THEN 'failed' ELSE "
                        "'queued' END,error=%s,next_retry_at=now()+(%s * interval '1 "
                        "second'),lease_owner=NULL,lease_expires_at=NULL,updated_at=now() WHERE "
                    )
                    + self._fence(),
                    (error, delay, *self._lease_args(job)),
                ).rowcount
                == 1
            )

    def cancel_job(self, job_id: UUID, team: str) -> bool:
        with self.pool.connection() as conn:
            return (
                conn.execute(
                    (
                        "UPDATE jobs SET "
                        "status='cancelled',cancel_requested=true,lease_owner=NULL,lease_expires_at=NULL,updated_at=now()"
                        " WHERE id=%s AND team=%s AND status IN ('queued','running')"
                    ),
                    (job_id, team),
                ).rowcount
                == 1
            )

    _REC = (
        "SELECT "
        "r.id,r.job_id,r.scenario_id,r.plan_json,r.evaluation_json,r.scenario_version,r.solution_digest"
        " FROM recommendations r JOIN scenarios s ON s.id=r.scenario_id "
    )

    @staticmethod
    def _rec_dict(row) -> dict:
        digest = row[6] or plan_digest(Plan.model_validate(row[3]))
        return {
            "id": str(row[0]),
            "job_id": str(row[1]),
            "scenario_id": row[2],
            "plan": row[3],
            "evaluation": row[4],
            "scenario_version": row[5],
            "solution_digest": digest,
        }

    def recommendation(self, recommendation_id: str, scenario_id: str, team: str):
        with self.pool.connection() as conn:
            row = conn.execute(
                self._REC + "WHERE r.id=%s AND r.scenario_id=%s AND s.team=%s",
                (recommendation_id, scenario_id, team),
            ).fetchone()
        return self._rec_dict(row) if row else None

    def bound_recommendation(
        self, recommendation_id: str, scenario_id: str, team: str
    ) -> Recommendation | None:
        try:
            UUID(recommendation_id)
        except ValueError:
            return None
        found = self.recommendation(recommendation_id, scenario_id, team)
        if found is None:
            return None
        return Recommendation(
            id=found["id"],
            scenario_version=found["scenario_version"],
            plan=Plan.model_validate(found["plan"]),
            digest=found["solution_digest"],
            objective_cents=found["evaluation"]["objective_cents"],
        )

    def recommendation_for_job(self, job_id: str, scenario_id: str, team: str):
        """Return the durable result attached to a job, scoped to its team."""
        with self.pool.connection() as conn:
            row = conn.execute(
                self._REC + "WHERE r.job_id=%s AND r.scenario_id=%s AND s.team=%s",
                (job_id, scenario_id, team),
            ).fetchone()
        return self._rec_dict(row) if row else None

    def job(self, job_id: str, scenario_id: str, team: str):
        with self.pool.connection() as conn:
            row = conn.execute(
                (
                    "SELECT id,scenario_id,status,attempts,error,scenario_version,diagnosis FROM jobs "
                    "WHERE id=%s AND scenario_id=%s AND team=%s"
                ),
                (job_id, scenario_id, team),
            ).fetchone()
        return (
            {
                "id": str(row[0]),
                "scenario_id": row[1],
                "status": row[2],
                "attempts": row[3],
                "error": row[4],
                "scenario_version": row[5],
                "diagnosis": row[6],
            }
            if row
            else None
        )

    def decisions(self, scenario_id: str, team: str) -> list[DecisionEntry]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT "
                + ",".join("d." + c for c in _DECISION_COLUMNS.split(","))
                + " FROM decisions d JOIN scenarios s ON s.id=d.scenario_id WHERE d.scenario_id=%s AND s.team=%s ORDER BY d.id",
                (scenario_id, team),
            ).fetchall()
        return [self._decision_entry(r) for r in rows]

    def replay_decision(
        self, scenario_id: str, team: str, idempotency_key: str, request_hash: str
    ) -> DecisionEntry | None:
        """Return the decision an earlier identical request created, if any."""
        with self.pool.connection() as conn:
            prior = conn.execute(
                (
                    "SELECT request_hash,decision_hash FROM decision_idempotency WHERE team=%s AND "
                    "scenario_id=%s AND idempotency_key=%s"
                ),
                (team, scenario_id, idempotency_key),
            ).fetchone()
            if prior is None:
                return None
            if prior[0] != request_hash:
                raise ValueError("idempotency key reused for a different decision")
            row = conn.execute(
                "SELECT "
                + _DECISION_COLUMNS
                + " FROM decisions WHERE scenario_id=%s AND hash=%s",
                (scenario_id, prior[1]),
            ).fetchone()
        if row is None:
            raise RuntimeError("decision idempotency record has no decision")
        return self._decision_entry(row)

    def append_decision(
        self,
        scenario: Scenario,
        team: str,
        entry: DecisionEntry,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
    ) -> tuple[DecisionEntry, bool]:
        with self.pool.connection() as conn:
            # The row lock makes previous_hash and the append-only hash chain linear,
            # and serializes decisions against scenario revisions.
            locked = conn.execute(
                "SELECT current_version FROM scenarios WHERE id=%s AND team=%s FOR UPDATE",
                (scenario.id, team),
            ).fetchone()
            if locked is None:
                raise ValueError("scenario not found for team")
            if idempotency_key is not None:
                if not request_hash:
                    raise ValueError("request hash is required with an idempotency key")
                prior = conn.execute(
                    (
                        "SELECT request_hash,decision_hash FROM decision_idempotency WHERE team=%s AND "
                        "scenario_id=%s AND idempotency_key=%s"
                    ),
                    (team, scenario.id, idempotency_key),
                ).fetchone()
                if prior:
                    if prior[0] != request_hash:
                        raise ValueError(
                            "idempotency key reused for a different decision"
                        )
                    row = conn.execute(
                        "SELECT "
                        + _DECISION_COLUMNS
                        + " FROM decisions WHERE scenario_id=%s AND hash=%s",
                        (scenario.id, prior[1]),
                    ).fetchone()
                    if row is None:
                        raise RuntimeError(
                            "decision idempotency record has no decision"
                        )
                    return self._decision_entry(row), False
            if (
                entry.scenario_version is not None
                and entry.scenario_version != locked[0]
            ):
                raise StaleScenario(
                    f"scenario is at version {locked[0]}, decision names {entry.scenario_version}"
                )
            prior = conn.execute(
                "SELECT hash FROM decisions WHERE scenario_id=%s ORDER BY id DESC LIMIT 1",
                (scenario.id,),
            ).fetchone()
            if (prior[0] if prior else "") != entry.previous_hash:
                raise ValueError("decision chain changed; retry the command")
            rec = None
            try:
                rec = UUID(entry.recommendation_id)
            except ValueError:
                if entry.action != "reject":
                    raise ValueError(
                        "recommendation_id must reference a persisted recommendation"
                    )
            if (
                rec
                and conn.execute(
                    "SELECT 1 FROM recommendations WHERE id=%s AND scenario_id=%s",
                    (rec, scenario.id),
                ).fetchone()
                is None
            ):
                # The locked scenario already established team ownership above.
                raise ValueError("recommendation does not belong to this scenario")
            conn.execute(
                "INSERT INTO decisions(scenario_id,"
                + _DECISION_COLUMNS
                + ") VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    scenario.id,
                    entry.action,
                    rec,
                    entry.actor,
                    entry.reason,
                    Jsonb(entry.plan.model_dump(mode="json")) if entry.plan else None,
                    entry.previous_hash,
                    entry.hash,
                    entry.scenario_version,
                    entry.solution_digest,
                    entry.objective_delta_cents,
                ),
            )
            if idempotency_key is not None:
                conn.execute(
                    (
                        "INSERT INTO "
                        "decision_idempotency(team,scenario_id,idempotency_key,request_hash,decision_hash) "
                        "VALUES(%s,%s,%s,%s,%s)"
                    ),
                    (team, scenario.id, idempotency_key, request_hash, entry.hash),
                )
            return entry, True

    @staticmethod
    def _decision_entry(row) -> DecisionEntry:
        recommendation_id = str(row[1]) if row[1] else ""
        return DecisionEntry(
            row[0],
            recommendation_id,
            row[2],
            row[3],
            Plan.model_validate(row[4]) if row[4] else None,
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
        )
