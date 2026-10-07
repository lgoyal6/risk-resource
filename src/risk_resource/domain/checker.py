from __future__ import annotations

from .models import Plan, PlanEvaluation, Scenario
from .objective import evaluate_objective


def evaluate_plan(scenario: Scenario, plan: Plan) -> PlanEvaluation:
    violations: list[str] = []
    orders = {w.id: w for w in scenario.work_orders}
    techs = {t.id: t for t in scenario.technicians}
    assets = {a.id: a for a in scenario.assets}
    if len({a.work_order_id for a in plan.assignments}) != len(plan.assignments):
        violations.append("work order assigned more than once")
    for a in plan.assignments:
        w, t = orders.get(a.work_order_id), techs.get(a.technician_id)
        if w is None:
            violations.append(f"unknown work order {a.work_order_id}")
            continue
        if t is None:
            violations.append(f"unknown technician {a.technician_id}")
            continue
        asset = assets[w.asset_id]
        if w.skill not in t.skills:
            violations.append(f"technician {t.id} lacks skill for {w.id}")
        if asset.site not in t.cleared_sites:
            violations.append(f"technician {t.id} not cleared for {asset.site}")
        if (
            not w.earliest_day <= a.day <= w.latest_day
            or a.day >= scenario.horizon_days
        ):
            violations.append(f"{w.id} outside scheduling window")
    for w in scenario.work_orders:
        if w.mandatory and w.id not in {a.work_order_id for a in plan.assignments}:
            violations.append(f"mandatory work order {w.id} is unscheduled")
        for predecessor in w.after:
            current = next(
                (a.day for a in plan.assignments if a.work_order_id == w.id), None
            )
            prior = next(
                (a.day for a in plan.assignments if a.work_order_id == predecessor),
                None,
            )
            if current is not None and (prior is None or prior >= current):
                violations.append(f"precedence violated: {predecessor} before {w.id}")
    for (tid, day), group in _groups(
        plan.assignments, lambda a: (a.technician_id, a.day)
    ):
        t = techs.get(tid)
        if (
            t
            and sum(
                (
                    orders[a.work_order_id].p50_minutes
                    if scenario.planning_quantile == "p50"
                    else orders[a.work_order_id].p90_minutes
                )
                for a in group
            )
            > t.available_minutes_per_day
        ):
            violations.append(f"technician {tid} exceeds capacity on day {day}")
    for (cluster, day), group in _groups(
        plan.assignments,
        lambda a: (assets[orders[a.work_order_id].asset_id].cluster, a.day),
    ):
        if len(
            {orders[a.work_order_id].asset_id for a in group}
        ) > scenario.max_offline_per_day.get(cluster, 0):
            violations.append(f"cluster {cluster} exceeds offline limit on day {day}")
    objective, risk, travel, overdue = evaluate_objective(scenario, plan.assignments)
    return PlanEvaluation(
        feasible=not violations,
        objective_cents=objective,
        risk_value_cents=risk,
        travel_cost_cents=travel,
        overdue_penalty_cents=overdue,
        violations=tuple(sorted(set(violations))),
    )


def _groups(items, key):
    result = {}
    for item in items:
        result.setdefault(key(item), []).append(item)
    return result.items()
