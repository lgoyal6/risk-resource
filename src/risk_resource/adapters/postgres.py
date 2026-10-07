from __future__ import annotations

import json
from pathlib import Path

from psycopg_pool import ConnectionPool

from risk_resource.application.services import scenario_hash
from risk_resource.domain.models import Scenario


class PostgresRepository:
    def __init__(self, dsn: str):
        self.pool = ConnectionPool(dsn, open=False)

    def open(self) -> None:
        self.pool.open()

    def close(self) -> None:
        self.pool.close()

    def migrate(self, directory: str = "migrations") -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations(version text primary key, applied_at timestamptz not null default now())"
            )
            for path in sorted(Path(directory).glob("*.sql")):
                version = path.name
                if conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=%s", (version,)
                ).fetchone():
                    continue
                conn.execute(path.read_text())
                conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES(%s)", (version,)
                )

    def save_scenario(self, scenario: Scenario, team: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO scenarios(id,team,name,provenance,raw_json,content_sha256) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    scenario.id,
                    team,
                    scenario.name,
                    scenario.provenance,
                    json.dumps(scenario.model_dump(mode="json")),
                    scenario_hash(scenario),
                ),
            )

    def get_scenario(self, scenario_id: str, team: str) -> Scenario | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT raw_json FROM scenarios WHERE id=%s AND team=%s",
                (scenario_id, team),
            ).fetchone()
        return Scenario.model_validate(row[0]) if row else None
