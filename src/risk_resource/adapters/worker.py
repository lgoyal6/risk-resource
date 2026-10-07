from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class JobLease:
    id: UUID
    scenario_id: str
    team: str
    owner: str
    attempt: int


class JobWorker:
    """Execute one durable lease; the repository fences every write by attempt."""

    def __init__(self, repository, owner: str, lease_seconds: int = 60):
        if not owner.strip() or lease_seconds < 1:
            raise ValueError("worker owner and positive lease duration are required")
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
            result = self.repository.solve_job(job)
            self.repository.complete_job(job, result)
        except Exception as exc:  # noqa: BLE001 - the job boundary persists any adapter failure
            # Store a diagnosis class, never a provider message that may contain credentials.
            self.repository.fail_job(job, type(exc).__name__)
        return True
