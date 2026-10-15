"""Approval rules that bind a decision to a scenario version and solution digest.

Storage-free so the same rules run in tests and behind the PostgreSQL adapter
(ADR 0008). The checker, not the solver, decides feasibility.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.decisions import DecisionEntry, append_decision
from risk_resource.domain.models import Plan, Scenario


class StaleScenario(ValueError):
    """The decision names a scenario version that is no longer current."""


class DigestMismatch(ValueError):
    """The plan does not match what the decision claims to adopt."""


def plan_digest(plan: Plan) -> str:
    assignments = sorted(
        (a.model_dump(mode="json") for a in plan.assignments),
        key=lambda a: (a["work_order_id"], a["technician_id"], a["day"]),
    )
    return sha256(
        json.dumps(
            {"assignments": assignments}, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Recommendation:
    id: str
    scenario_version: int
    plan: Plan
    digest: str
    objective_cents: int


def bound_decision(
    entries: tuple[DecisionEntry, ...],
    *,
    action: str,
    actor: str,
    reason: str,
    plan: Plan | None,
    requested_version: int,
    current_version: int,
    current: Scenario,
    recommendation: Recommendation | None,
    recommendation_id: str,
) -> DecisionEntry:
    """Validate a decision against the current head and return the chained entry."""
    if requested_version != current_version:
        raise StaleScenario(
            f"scenario is at version {current_version}, decision names {requested_version}"
        )
    digest: str | None = None
    delta: int | None = None
    if action in {"approve", "override", "rollback"} and plan is None:
        raise ValueError("plan is required")
    if action in {"approve", "override"}:
        assert plan is not None
        if recommendation is None:
            raise ValueError("recommendation must be a persisted result")
        if recommendation.scenario_version != current_version:
            raise StaleScenario(
                f"recommendation was solved for version {recommendation.scenario_version}, "
                f"scenario is at version {current_version}"
            )
        digest = plan_digest(plan)
        if action == "approve" and digest != recommendation.digest:
            raise DigestMismatch("approve must adopt the recommendation unchanged")
        if action == "override":
            if digest == recommendation.digest:
                raise DigestMismatch("override plan is identical to the recommendation")
            evaluation = evaluate_plan(current, plan)
            if evaluation.feasible:
                delta = evaluation.objective_cents - recommendation.objective_cents
    elif action == "rollback":
        assert plan is not None
        digest = plan_digest(plan)
        adopted = {
            plan_digest(e.plan)
            for e in entries
            if e.action in {"approve", "override"} and e.plan is not None
        }
        if digest not in adopted:
            raise DigestMismatch("rollback must re-adopt an earlier approved plan")
    # append_decision enforces reasons, plan presence and checker feasibility.
    return append_decision(
        entries,
        action=action,
        recommendation_id=recommendation_id,
        actor=actor,
        reason=reason,
        plan=plan,
        scenario=current,
        scenario_version=current_version,
        solution_digest=digest,
        objective_delta_cents=delta,
    )
