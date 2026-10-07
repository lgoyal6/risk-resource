from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID


@dataclass(frozen=True)
class JobLease:
    job_id: UUID
    owner: str
    expires_in: timedelta


class JobWorker:
    """Protocol-level worker contract; SQL claim uses SKIP LOCKED and fencing."""

    def __init__(self, repository, owner: str, lease_seconds: int = 60):
        self.repository, self.owner, self.lease_seconds = (
            repository,
            owner,
            lease_seconds,
        )

    def run_once(self):
        job = self.repository.claim_job(self.owner, self.lease_seconds)
        if job is None:
            return False
        try:
            result = self.repository.solve_job(job, self.owner)
            self.repository.complete_job(job.id, self.owner, result)
        except RuntimeError as exc:
            self.repository.fail_job(job.id, self.owner, str(exc))
        return True
