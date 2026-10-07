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


def append_decision(
    entries: tuple[DecisionEntry, ...],
    *,
    action: str,
    recommendation_id: str,
    actor: str,
    reason: str,
    plan: Plan | None = None,
    scenario: Scenario | None = None,
) -> DecisionEntry:
    if action in {"override", "reject", "rollback"} and not reason.strip():
        raise ValueError("reason is required")
    if action in {"approve", "override", "rollback"} and plan is None:
        raise ValueError("plan is required")
    if scenario and plan and not evaluate_plan(scenario, plan).feasible:
        raise ValueError("decision plan is infeasible")
    previous = entries[-1].hash if entries else ""
    payload = {
        "action": action,
        "recommendation_id": recommendation_id,
        "actor": actor,
        "reason": reason,
        "plan": plan.model_dump(mode="json") if plan else None,
        "previous_hash": previous,
    }
    digest = sha256(
        (previous + json.dumps(payload, sort_keys=True, separators=(",", ":"))).encode()
    ).hexdigest()
    return DecisionEntry(
        action=action,
        recommendation_id=recommendation_id,
        actor=actor,
        reason=reason,
        plan=plan,
        previous_hash=previous,
        hash=digest,
    )


def verify_decisions(entries: tuple[DecisionEntry, ...]) -> tuple[bool, int | None]:
    previous = ""
    for index, entry in enumerate(entries):
        payload = {
            "action": entry.action,
            "recommendation_id": entry.recommendation_id,
            "actor": entry.actor,
            "reason": entry.reason,
            "plan": entry.plan.model_dump(mode="json") if entry.plan else None,
            "previous_hash": previous,
        }
        expected = sha256(
            (
                previous + json.dumps(payload, sort_keys=True, separators=(",", ":"))
            ).encode()
        ).hexdigest()
        if entry.previous_hash != previous or entry.hash != expected:
            return False, index
        previous = entry.hash
    return True, None
