import hashlib
import json

from fastapi.testclient import TestClient

from risk_resource.transport.api import create_app


def key_for(monkeypatch, user: str, team: str, role: str, token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()
    monkeypatch.setenv(
        "RR_API_KEYS", json.dumps({digest: {"user": user, "team": team, "role": role}})
    )
    return token


def test_public_workflow(monkeypatch):
    token = key_for(monkeypatch, "planner", "demo", "approver", "test-token")
    client = TestClient(create_app())
    headers = {"X-API-Key": token}
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/scenarios", headers=headers).status_code == 200
    assert client.post("/scenarios/demo-week/baseline", headers=headers).json()[
        "evaluation"
    ]["feasible"]
    assert client.post("/scenarios/demo-week/solve", headers=headers).json()[
        "evaluation"
    ]["feasible"]


def test_missing_auth_and_cross_team_are_denied(monkeypatch):
    token = key_for(monkeypatch, "planner", "other-team", "approver", "other-token")
    client = TestClient(create_app())
    assert client.get("/scenarios").status_code == 401
    assert (
        client.get("/scenarios/demo-week", headers={"X-API-Key": token}).status_code
        == 404
    )


def test_decision_uses_authenticated_actor_and_replays_idempotently(monkeypatch):
    token = key_for(monkeypatch, "approver-user", "demo", "approver", "approve-token")
    client = TestClient(create_app())
    headers = {"X-API-Key": token, "Idempotency-Key": "decision-1"}
    plan = client.post("/scenarios/demo-week/solve", headers=headers).json()["plan"]
    body = {
        "action": "approve",
        "recommendation_id": "baseline",
        "actor": "forged",
        "reason": "checked",
        "plan": plan,
    }
    first = client.post("/scenarios/demo-week/decisions", headers=headers, json=body)
    second = client.post("/scenarios/demo-week/decisions", headers=headers, json=body)
    assert first.status_code == 201
    assert first.json()["actor"] == "approver-user"
    assert second.status_code == 200
    assert second.json()["hash"] == first.json()["hash"]


def test_key_reuse_for_another_command_is_rejected(monkeypatch):
    token = key_for(monkeypatch, "user", "demo", "approver", "test-token")
    client = TestClient(create_app())
    headers = {"X-API-Key": token, "Idempotency-Key": "decision"}
    body = {"action": "reject", "recommendation_id": "rec", "reason": "not suitable"}
    assert (
        client.post(
            "/scenarios/demo-week/decisions", json=body, headers=headers
        ).status_code
        == 201
    )
    body["reason"] = "different reason"
    assert (
        client.post(
            "/scenarios/demo-week/decisions", json=body, headers=headers
        ).status_code
        == 409
    )


def test_competing_retries_append_one_hash_chain_entry(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    token = key_for(monkeypatch, "user", "demo", "approver", "retry-token")
    client = TestClient(create_app())
    headers = {"X-API-Key": token, "Idempotency-Key": "race"}
    body = {"action": "reject", "recommendation_id": "rec", "reason": "not suitable"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda _: client.post(
                    "/scenarios/demo-week/decisions", json=body, headers=headers
                ),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [200, 201]
    assert responses[0].json() == responses[1].json()
    assert client.get(
        "/scenarios/demo-week/decisions/verify", headers=headers
    ).json() == {"valid": True}


def test_unconfigured_keys_and_invalid_body_framing(monkeypatch):
    monkeypatch.delenv("RR_API_KEYS", raising=False)
    client = TestClient(create_app())
    assert client.get("/scenarios").status_code == 503
    assert (
        client.post(
            "/scenarios", content=b"x", headers={"Content-Length": "bad"}
        ).status_code
        == 400
    )
    response = client.post("/scenarios", content=iter([b"x" * (1024 * 1024)] * 3))
    assert response.status_code == 413
