#!/usr/bin/env bash
set -euo pipefail
# An isolated test container avoids local application ports, volumes, and credentials.
rr_gate_name="rr-postgres-gate-$$"
cleanup() { docker rm -f "$rr_gate_name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$rr_gate_name" --memory 256m --cpus 1 \
  -e POSTGRES_DB=risk_resource -e POSTGRES_USER=risk_resource \
  -e POSTGRES_PASSWORD=gate-only-password -p 127.0.0.1::5432 postgres:16.4 >/dev/null
for _ in $(seq 1 30); do
  if docker exec "$rr_gate_name" pg_isready -U risk_resource -d risk_resource >/dev/null 2>&1; then break; fi
  sleep 1
done
rr_gate_port=$(docker port "$rr_gate_name" 5432/tcp | sed 's/.*://')
export RR_POSTGRES_DSN="postgresql://risk_resource:gate-only-password@127.0.0.1:$rr_gate_port/risk_resource"
uv run --frozen pytest -q tests/test_postgres_integration.py
