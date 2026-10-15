from __future__ import annotations

import argparse
import os
import time

from risk_resource.adapters.postgres import PostgresRepository
from risk_resource.adapters.worker import JobWorker


def run_worker(repository, owner: str = "worker-1") -> int:
    """Drain currently available jobs; useful for embedding and one-shot work."""
    worker = JobWorker(repository, owner)
    count = 0
    while worker.run_once():
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the PostgreSQL solve worker")
    parser.add_argument(
        "--once", action="store_true", help="process at most one queued job and exit"
    )
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        parser.error("DATABASE_URL is required")
    owner = os.environ.get("RR_WORKER_ID", "worker-1")
    repository = PostgresRepository(dsn)
    repository.open()
    try:
        worker = JobWorker(repository, owner)
        if args.once:
            worker.run_once()
            return 0
        while True:
            if not worker.run_once():
                time.sleep(1)
    except KeyboardInterrupt:
        return 0
    finally:
        repository.close()


if __name__ == "__main__":
    raise SystemExit(main())
