"""Real PostgreSQL worker checks, each in a uniquely owned schema."""

import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from risk_resource.adapters.postgres import PostgresRepository
from risk_resource.adapters.worker import JobWorker
from risk_resource.sample import demo_scenario

pytestmark = pytest.mark.integration


@pytest.fixture
def repository():
    dsn = os.getenv("RR_POSTGRES_DSN")
    if not dsn:
        pytest.skip("RR_POSTGRES_DSN is not configured")
    schema = "gate_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    repo = PostgresRepository(make_conninfo(dsn, options=f"-c search_path={schema}"))
    try:
        repo.open()
        repo.migrate()
        yield repo
    finally:
        repo.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def seed(repo, attempts=3):
    scenario = demo_scenario()
    repo.save_scenario(scenario, "demo")
    return repo.enqueue_job(scenario.id, "demo", "request", attempts)


def expire(repo, job_id):
    with repo.pool.connection() as conn:
        conn.execute(
            "UPDATE jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s",
            (job_id,),
        )


def test_scenario_round_trip_and_team_isolation(repository):
    scenario = demo_scenario()
    repository.save_scenario(scenario, "demo")
    assert repository.get_scenario(scenario.id, "demo") == scenario
    assert repository.get_scenario(scenario.id, "other") is None
    with pytest.raises(ValueError):
        repository.enqueue_job(scenario.id, "other", "request")


def test_actual_worker_persists_recommendation_and_success_together(repository):
    jid = seed(repository)
    assert JobWorker(repository, "worker").run_once()
    with repository.pool.connection() as conn:
        row = conn.execute(
            "SELECT j.status,r.plan_json,r.evaluation_json FROM jobs j JOIN recommendations r ON r.job_id=j.id WHERE j.id=%s",
            (jid,),
        ).fetchone()
    assert row[0] == "succeeded"
    assert row[1]["assignments"]
    assert row[2]["feasible"]
    recommendation = repository.recommendation_for_job(str(jid), "demo-week", "demo")
    assert recommendation is not None
    assert recommendation["job_id"] == str(jid)
    assert not JobWorker(repository, "worker").run_once()


def test_competing_claims_and_expired_same_owner_are_fenced(repository):
    jid = seed(repository)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: repository.claim_job("worker", 60), range(2)))
    assert sum(lease is not None for lease in claims) == 1
    old = next(lease for lease in claims if lease is not None)
    expire(repository, jid)
    result = repository.solve_job(old)
    assert not repository.complete_job(old, result)
    assert not repository.heartbeat(old, 60)
    new = repository.claim_job("worker", 60)
    assert new.attempt == old.attempt + 1
    assert not repository.complete_job(old, result)
    assert not repository.fail_job(old, "stale")
    assert repository.complete_job(new, result)


def test_result_insert_failure_rolls_back_success(repository):
    jid = seed(repository)
    lease = repository.claim_job("worker", 60)
    result = repository.solve_job(lease)
    with repository.pool.connection() as conn:
        conn.execute(
            "INSERT INTO recommendations(id,job_id,scenario_id,plan_json,evaluation_json) VALUES(%s,%s,%s,'{}','{}')",
            (uuid4(), jid, lease.scenario_id),
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        repository.complete_job(lease, result)
    with repository.pool.connection() as conn:
        assert (
            conn.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()[0]
            == "running"
        )


def test_final_attempt_crash_becomes_terminal(repository):
    jid = seed(repository, attempts=1)
    assert repository.claim_job("crashed", 60)
    expire(repository, jid)
    assert repository.claim_job("replacement", 60) is None
    with repository.pool.connection() as conn:
        assert (
            conn.execute("SELECT status FROM jobs WHERE id=%s", (jid,)).fetchone()[0]
            == "failed"
        )


def test_retry_backoff_and_cancellation_are_durable(repository):
    jid = seed(repository)
    lease = repository.claim_job("worker", 60)
    assert repository.fail_job(lease, "TimeoutError")
    assert repository.claim_job("worker", 60) is None
    with repository.pool.connection() as conn:
        conn.execute(
            "UPDATE jobs SET next_retry_at=now()-interval '1 second' WHERE id=%s",
            (jid,),
        )
    resumed = repository.claim_job("worker", 60)
    assert resumed.attempt == 2
    assert not repository.cancel_job(jid, "other")
    assert repository.cancel_job(jid, "demo")
    assert not repository.complete_job(resumed, repository.solve_job(resumed))
    assert repository.claim_job("worker", 60) is None


def test_approval_audit_rows_reject_mutation(repository):
    seed(repository)
    with repository.pool.connection() as conn:
        conn.execute(
            "INSERT INTO decisions(scenario_id,action,actor,reason,previous_hash,hash) VALUES('demo-week','reject','reviewer','checked','','hash')"
        )
    for sql in ("UPDATE decisions SET reason='altered'", "DELETE FROM decisions"):
        with pytest.raises(psycopg.Error), repository.pool.connection() as conn:
            conn.execute(sql)
