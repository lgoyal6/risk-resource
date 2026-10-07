#!/usr/bin/env bash
set -euo pipefail
compose_file="infra/docker-compose.yml"
cleanup() { docker compose -f "$compose_file" down -v; }
trap cleanup EXIT
docker compose -f "$compose_file" up -d postgres
for _ in $(seq 1 30); do
  if docker compose -f "$compose_file" exec -T postgres pg_isready -U risk_resource -d risk_resource >/dev/null 2>&1; then break; fi
  sleep 1
done
export RR_POSTGRES_DSN="postgresql://risk_resource:risk_resource@localhost:5432/risk_resource"
uv run pytest -q tests/test_postgres_integration.py
