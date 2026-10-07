from __future__ import annotations

import os

from risk_resource.adapters.postgres import PostgresRepository

if __name__ == "__main__":
    repo = PostgresRepository(os.environ["DATABASE_URL"])
    repo.open()
    repo.migrate("/app/migrations")
    repo.close()
