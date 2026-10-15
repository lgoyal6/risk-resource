"""Check the durable API and repository against isolated PostgreSQL schemas.
Exercises API recreation, decision fencing and the JobWorker result boundary.
Each schema is disposable and uses only the caller-provided local test DSN.
"""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from itertools import product
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from risk_resource.adapters.postgres import PostgresRepository
from risk_resource.adapters.worker import JobWorker
from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.models import Assignment, Plan, Scenario
from risk_resource.transport.api import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def durable_api(monkeypatch):
    dsn = os.getenv("RR_POSTGRES_DSN")
    if not dsn:
        pytest.skip("RR_POSTGRES_DSN is not configured")
    schema = "api_gate_" + uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped_dsn = make_conninfo(dsn, options=f"-c search_path={schema}")
    monkeypatch.setenv("DATABASE_URL", scoped_dsn)
    token = "persistent-api-test-token"
    monkeypatch.setenv(
        "RR_API_KEYS",
        json.dumps(
            {
                hashlib.sha256(token.encode()).hexdigest(): {
                    "user": "reviewer",
                    "team": "demo",
                    "role": "approver",
                }
            }
        ),
    )
    try:
        yield scoped_dsn, token
    finally:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')


def test_http_solve_approval_and_revision_survive_api_restart(durable_api):
    dsn, token = durable_api
    headers = {"X-API-Key": token, "Idempotency-Key": "solve-1"}

    # Queue through one API instance, then discard it before a worker and a
    # second API instance recover the durable state.
    with TestClient(create_app()) as first:
        queued = first.post("/scenarios/demo-week/solve", headers=headers)
        assert queued.status_code == 200
        job_id = queued.json()["job_id"]
        assert first.get(f"/scenarios/demo-week/jobs/{job_id}", headers=headers).json()[
            "job"
        ]["status"] == "queued"

    worker_repo = PostgresRepository(dsn)
    worker_repo.open()
    try:
        assert JobWorker(worker_repo, "api-test-worker").run_once()
    finally:
        worker_repo.close()

    with TestClient(create_app()) as restarted:
        status = restarted.get(
            f"/scenarios/demo-week/jobs/{job_id}", headers=headers
        )
        assert status.status_code == 200
        assert status.json()["job"]["status"] == "succeeded"
        recommendation = status.json()["recommendation"]
        assert recommendation["scenario_version"] == 1

        scenario = Scenario.model_validate(
            restarted.get("/scenarios/demo-week", headers=headers).json()
        )
        original_plan = Plan.model_validate(recommendation["plan"])
        override_plan = None
        for index, assignment in enumerate(original_plan.assignments):
            for technician_id, day in product(
                (tech.id for tech in scenario.technicians),
                range(scenario.horizon_days),
            ):
                candidate = list(original_plan.assignments)
                candidate[index] = Assignment(
                    work_order_id=assignment.work_order_id,
                    technician_id=technician_id,
                    day=day,
                )
                proposed = Plan(assignments=tuple(candidate))
                if (
                    proposed != original_plan
                    and evaluate_plan(scenario, proposed).feasible
                ):
                    override_plan = proposed
                    break
            if override_plan is not None:
                break
        assert override_plan is not None, "fixture must admit a feasible override"

        override_headers = {**headers, "Idempotency-Key": "override-1"}
        override_body = {
            "action": "override",
            "recommendation_id": recommendation["id"],
            "reason": "tested a feasible alternate assignment",
            "plan": override_plan.model_dump(mode="json"),
            "scenario_version": 1,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            overrides = list(
                pool.map(
                    lambda _: restarted.post(
                        "/scenarios/demo-week/decisions",
                        headers=override_headers,
                        json=override_body,
                    ),
                    range(2),
                )
            )
        assert sorted(response.status_code for response in overrides) == [200, 201]
        assert overrides[0].json() == overrides[1].json()

        def submit_competing_override(key):
            return restarted.post(
                "/scenarios/demo-week/decisions",
                headers={**headers, "Idempotency-Key": key},
                json=override_body,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            competing = list(
                pool.map(submit_competing_override, ("override-race-a", "override-race-b"))
            )
        assert sorted(response.status_code for response in competing) == [201, 409]

        decision_headers = {**headers, "Idempotency-Key": "approve-1"}
        decision_body = {
            "action": "approve",
            "recommendation_id": recommendation["id"],
            "reason": "reviewed",
            "plan": recommendation["plan"],
            "scenario_version": 1,
        }
        created = restarted.post(
            "/scenarios/demo-week/decisions",
            headers=decision_headers,
            json=decision_body,
        )
        replayed = restarted.post(
            "/scenarios/demo-week/decisions",
            headers=decision_headers,
            json=decision_body,
        )
        assert created.status_code == 201
        assert replayed.status_code == 200
        assert replayed.json() == created.json()

        scenario = restarted.get("/scenarios/demo-week", headers=headers).json()
        scenario["name"] += " revised"
        revised = restarted.post(
            "/scenarios/demo-week/versions",
            headers=headers,
            json={"expected_version": 1, "scenario": scenario},
        )
        assert revised.status_code == 201
        assert revised.json()["version"] == 2

        stale = restarted.post(
            "/scenarios/demo-week/decisions",
            headers={**headers, "Idempotency-Key": "approve-stale"},
            json=decision_body,
        )
        assert stale.status_code == 409
        assert restarted.get(
            "/scenarios/demo-week/decisions/verify", headers=headers
        ).json() == {"valid": True}

    # Recreate the API after the approval and override commit, then recover the
    # decision chain from PostgreSQL rather than process memory.
    with TestClient(create_app()) as recovered:
        assert recovered.get(
            "/scenarios/demo-week/decisions/verify", headers=headers
        ).json() == {"valid": True}
        history = recovered.get(
            "/scenarios/demo-week/decisions", headers=headers
        ).json()
        assert [entry["action"] for entry in history] == [
            "override",
            "override",
            "approve",
        ]


def test_ready_reports_database_pool_failure(monkeypatch, durable_api):
    from risk_resource.adapters.postgres import PostgresRepository

    _dsn, token = durable_api
    opened = []
    original_open = PostgresRepository.open

    def capture_open(repository):
        original_open(repository)
        opened.append(repository)

    monkeypatch.setattr(PostgresRepository, "open", capture_open)
    with TestClient(create_app()) as client:
        assert client.get("/ready", headers={"X-API-Key": token}).status_code == 200
        assert len(opened) == 1
        opened[0].close()
        failed = client.get("/ready", headers={"X-API-Key": token})
        assert failed.status_code == 503
        assert failed.json()["detail"].startswith("database unavailable:")


def test_decision_append_uses_one_pool_connection(durable_api):
    """A one-slot pool can commit an approval without nested checkout starvation."""
    from psycopg_pool import ConnectionPool

    from risk_resource.application.approvals import bound_decision
    dsn, _token = durable_api
    repo = PostgresRepository(dsn)
    repo.pool = ConnectionPool(dsn, min_size=1, max_size=1, timeout=0.2, open=False)
    repo.open()
    try:
        repo.migrate()
        from risk_resource.sample import demo_scenario
        scenario = demo_scenario()
        repo.save_scenario(scenario, "demo")
        job_id = repo.enqueue_job(scenario.id, "demo", "one-slot")
        assert JobWorker(repo, "one-slot-worker").run_once()
        rec = repo.recommendation_for_job(str(job_id), scenario.id, "demo")
        recommendation = repo.bound_recommendation(rec["id"], scenario.id, "demo")
        entry = bound_decision((), action="approve", actor="reviewer", reason="reviewed",
            plan=Plan.model_validate(rec["plan"]), requested_version=1, current_version=1,
            current=scenario, recommendation=recommendation, recommendation_id=rec["id"])
        saved, created = repo.append_decision(scenario, "demo", entry)
        assert created and saved.hash == entry.hash
        assert len(repo.decisions(scenario.id, "demo")) == 1
    finally:
        repo.close()
