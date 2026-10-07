from fastapi.testclient import TestClient

from risk_resource.transport.api import app


def test_public_workflow():
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/scenarios").status_code == 200
    assert client.post("/scenarios/demo-week/baseline").json()["evaluation"]["feasible"]
    assert client.post("/scenarios/demo-week/solve").json()["evaluation"]["feasible"]
