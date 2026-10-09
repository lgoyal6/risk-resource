#!/usr/bin/env python3
"""Run the shipped image and migrations, then exercise role and team boundaries."""
import hashlib
import http.client
import json
import subprocess
import time
import uuid

name = "risk-resource-gate-" + uuid.uuid4().hex[:10]
database = name + "-db"
image = name + ":test"

def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()

def request(port, method, path, body=None, token=None, key=None):
    headers = {}
    if token:
        headers["X-API-Key"] = token
    if key:
        headers["Idempotency-Key"] = key
    if body is not None:
        body = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body, headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()

try:
    subprocess.run(["docker", "build", "-t", image, "."], check=True)
    docker("network", "create", name)
    docker("run", "-d", "--name", database, "--network", name,
           "--memory", "256m", "--cpus", "1", "-e", "POSTGRES_DB=gate",
           "-e", "POSTGRES_PASSWORD=gate-only-password", "postgres:16.4")
    for _ in range(30):
        if subprocess.run(["docker", "exec", database, "pg_isready", "-U", "postgres", "-d", "gate"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            break
        time.sleep(1)
    tokens = {hashlib.sha256(token.encode()).hexdigest(): {"user": token, "team": team, "role": role}
              for token, team, role in (("approver-token", "demo", "approver"), ("viewer-token", "demo", "viewer"), ("other-token", "other", "approver"))}
    docker("run", "-d", "--name", name, "--network", name, "--memory", "512m", "--cpus", "1",
           "-p", "127.0.0.1::8000", "-e", "RR_API_KEYS=" + json.dumps(tokens),
           "-e", f"DATABASE_URL=postgresql://postgres:gate-only-password@{database}:5432/gate", image)
    port = int(docker("port", name, "8000/tcp").rsplit(":", 1)[1])
    for _ in range(30):
        try:
            if request(port, "GET", "/health")[0] == 200:
                break
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("API failed readiness")
    assert request(port, "GET", "/scenarios")[0] == 401
    assert request(port, "GET", "/scenarios/demo-week", token="other-token")[0] == 404
    assert request(port, "POST", "/scenarios/demo-week/solve", token="viewer-token")[0] == 403
    status, solve = request(port, "POST", "/scenarios/demo-week/solve", token="approver-token")
    assert status == 200 and solve["status"] == "queued"
    # The persistent API returns a job, so exercise the same worker boundary a
    # deployment uses before reading the recommendation for approval.
    docker(
        "exec", name, ".venv/bin/python", "-c",
        "import os; from risk_resource.adapters.postgres import PostgresRepository; "
        "from risk_resource.adapters.worker import JobWorker; "
        "r=PostgresRepository(os.environ['DATABASE_URL']); r.open(); r.migrate(); "
        "JobWorker(r, 'container-gate').run_once(); r.close()",
    )
    status, completed = request(
        port, "GET", f"/scenarios/demo-week/jobs/{solve['job_id']}", token="approver-token"
    )
    assert status == 200 and completed["job"]["status"] == "succeeded"
    recommendation = completed["recommendation"]
    assert recommendation["evaluation"]["feasible"]
    body = {"action": "approve", "recommendation_id": recommendation["id"], "plan": recommendation["plan"], "actor": "spoof"}
    path = "/scenarios/demo-week/decisions"
    status, decision = request(port, "POST", path, body, "approver-token", "retry-key")
    assert status == 201 and decision["actor"] == "approver-token"
    status, replay = request(port, "POST", path, body, "approver-token", "retry-key")
    assert status == 200 and replay == decision
    body["reason"] = "changed request"
    assert request(port, "POST", path, body, "approver-token", "retry-key")[0] == 409
    assert docker("exec", database, "psql", "-U", "postgres", "-d", "gate", "-Atc", "select count(*) from schema_migrations") == "3"
    print("Container verified: migrations, roles, team isolation, actor binding, idempotent decisions")
except Exception:
    subprocess.run(["docker", "logs", name], check=False)
    raise
finally:
    for command in (("rm", "-f", name, database), ("network", "rm", name), ("image", "rm", image)):
        subprocess.run(["docker", *command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
