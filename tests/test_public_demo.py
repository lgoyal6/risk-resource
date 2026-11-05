from fastapi.testclient import TestClient

from risk_resource.transport.demo_api import app


def test_public_demo_runs_fixed_fixture_without_credentials_or_writes(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RR_API_KEYS", raising=False)
    response = TestClient(app).get("/api/demo")

    assert response.status_code == 200
    body = response.json()
    assert body["evidence_boundary"].startswith("Synthetic demo-week fixture")
    assert body["scenario"]["provenance"] == "demo"
    assert body["baseline"]["evaluation"]["feasible"] is True
    assert body["optimized"]["evaluation"]["feasible"] is True
