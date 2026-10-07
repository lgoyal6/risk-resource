import hashlib
import json

from fastapi.testclient import TestClient

from risk_resource.adapters.worker import JobWorker
from risk_resource.transport.api import create_app


def test_comparison_and_audit_endpoints(monkeypatch):
    token = "platform-secret"
    digest = hashlib.sha256(token.encode()).hexdigest()

    monkeypatch.setenv(
        "RR_API_KEYS",
        json.dumps({digest: {"user": "ops", "team": "demo", "role": "approver"}}),
    )
    client = TestClient(create_app())
    headers = {"x-api-key": token}
    assert client.get("/").status_code == 200
    assert (
        client.get("/scenarios/demo-week/compare", headers=headers).status_code == 200
    )
    assert (
        client.get("/scenarios/demo-week/sensitivity", headers=headers).status_code
        == 200
    )
    result = client.post("/scenarios/demo-week/baseline", headers=headers).json()
    entry = {
        "action": "approve",
        "recommendation_id": "rec-1",
        "actor": "ops",
        "reason": "",
        "plan": result["plan"],
    }
    assert (
        client.post(
            "/scenarios/demo-week/decisions",
            json=entry,
            headers={**headers, "Idempotency-Key": "platform-1"},
        ).status_code
        == 201
    )
    assert client.get(
        "/scenarios/demo-week/decisions/verify", headers=headers
    ).json() == {"valid": True}


def test_api_key_roles(monkeypatch):
    digest = hashlib.sha256(b"secret").hexdigest()
    monkeypatch.setenv(
        "RR_API_KEYS",
        json.dumps({digest: {"user": "u", "team": "t", "role": "viewer"}}),
    )
    client = TestClient(create_app())
    assert (
        client.post(
            "/scenarios",
            json={
                "id": "x",
                "name": "x",
                "provenance": "demo",
                "horizon_days": 1,
                "max_offline_per_day": {},
                "assets": [],
                "technicians": [],
                "work_orders": [],
            },
            headers={"x-api-key": "secret"},
        ).status_code
        == 403
    )


def test_worker_claims_and_fences():
    class Repo:
        def __init__(self):
            self.claimed = False
            self.completed = False

        def claim_job(self, owner, lease):
            self.claimed = True
            return type("J", (), {"id": "j"}) if not self.completed else None

        def solve_job(self, job):
            return "ok"

        def complete_job(self, job, result):
            self.completed = True

        def fail_job(self, job, error):
            raise AssertionError(error)

    repo = Repo()
    worker = JobWorker(repo, "w")
    assert worker.run_once()
    assert repo.completed


def test_request_body_limit(monkeypatch):

    monkeypatch.setenv(
        "RR_API_KEYS",
        json.dumps({"0" * 64: {"user": "u", "team": "demo", "role": "viewer"}}),
    )
    client = TestClient(create_app())
    response = client.post(
        "/scenarios", content=b"x", headers={"content-length": str(2 * 1024 * 1024 + 1)}
    )
    assert response.status_code == 413
