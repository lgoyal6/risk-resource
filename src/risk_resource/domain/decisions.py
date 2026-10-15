from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from .checker import evaluate_plan
from .models import Plan, Scenario


@dataclass(frozen=True)
class DecisionEntry:
    action: str
    recommendation_id: str
    actor: str
    reason: str
    plan: Plan | None
    previous_hash: str
    hash: str
    # Bound fields (ADR 0008). None on entries written before versioning.
    scenario_version: int | None = None
    solution_digest: str | None = None
    objective_delta_cents: int | None = None


_BOUND_FIELDS = ("scenario_version", "solution_digest", "objective_delta_cents")


def _digest(previous: str, payload: dict) -> str:
    return sha256(
        (previous + json.dumps(payload, sort_keys=True, separators=(",", ":"))).encode()
    ).hexdigest()


def _payload(
    *,
    action: str,
    recommendation_id: str,
    actor: str,
    reason: str,
    plan: Plan | None,
    previous_hash: str,
    bound: dict,
) -> dict:
    payload = {
        "action": action,
        "recommendation_id": recommendation_id,
        "actor": actor,
        "reason": reason,
        "plan": plan.model_dump(mode="json") if plan else None,
        "previous_hash": previous_hash,
    }
    # Only present fields are hashed, so legacy chains keep verifying.
    payload.update({k: v for k, v in bound.items() if v is not None})
    return payload


def append_decision(
    entries: tuple[DecisionEntry, ...],
    *,
    action: str,
    recommendation_id: str,
    actor: str,
    reason: str,
    plan: Plan | None = None,
    scenario: Scenario | None = None,
    scenario_version: int | None = None,
    solution_digest: str | None = None,
    objective_delta_cents: int | None = None,
) -> DecisionEntry:
    if action in {"override", "reject", "rollback"} and not reason.strip():
        raise ValueError("reason is required")
    if action in {"approve", "override", "rollback"} and plan is None:
        raise ValueError("plan is required")
    if scenario and plan and not evaluate_plan(scenario, plan).feasible:
        raise ValueError("decision plan is infeasible")
    previous = entries[-1].hash if entries else ""
    bound: dict[str, int | str | None] = {
        "scenario_version": scenario_version,
        "solution_digest": solution_digest,
        "objective_delta_cents": objective_delta_cents,
    }
    payload = _payload(
        action=action,
        recommendation_id=recommendation_id,
        actor=actor,
        reason=reason,
        plan=plan,
        previous_hash=previous,
        bound=bound,
    )
    return DecisionEntry(
        action=action,
        recommendation_id=recommendation_id,
        actor=actor,
        reason=reason,
        plan=plan,
        previous_hash=previous,
        hash=_digest(previous, payload),
        scenario_version=scenario_version,
        solution_digest=solution_digest,
        objective_delta_cents=objective_delta_cents,
    )


def verify_decisions(entries: tuple[DecisionEntry, ...]) -> tuple[bool, int | None]:
    previous = ""
    for index, entry in enumerate(entries):
        payload = _payload(
            action=entry.action,
            recommendation_id=entry.recommendation_id,
            actor=entry.actor,
            reason=entry.reason,
            plan=entry.plan,
            previous_hash=previous,
            bound={k: getattr(entry, k) for k in _BOUND_FIELDS},
        )
        if entry.previous_hash != previous or entry.hash != _digest(previous, payload):
            return False, index
        previous = entry.hash
    return True, None
