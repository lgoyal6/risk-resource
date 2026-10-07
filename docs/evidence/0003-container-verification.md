# Evidence 0003: containerized deployment

Date: 2026-10-07

- `docker build -t risk-resource:verify .` completed successfully from a clean Dockerfile build context.
- `scripts/verify_postgres.sh` passed against PostgreSQL 16.4.
- `docker compose -f infra/docker-compose.yml up -d --build` started both services; `curl http://localhost:8000/health` returned `{"status":"ok"}`.
- Compose teardown removed the test containers, network, and volume after verification.
