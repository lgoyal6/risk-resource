from __future__ import annotations

from .checker import evaluate_plan
from .models import Assignment, Plan, Scenario
from .objective import risk_value_cents


def greedy_plan(scenario: Scenario) -> Plan:
    orders = sorted(
        scenario.work_orders,
        key=lambda w: (
            not w.mandatory,
            -risk_value_cents(scenario, w, w.earliest_day),
            w.id,
        ),
    )
    selected: list[Assignment] = []
    for order in orders:
        candidates = []
        for tech in scenario.technicians:
            for day in range(
                order.earliest_day, min(order.latest_day, scenario.horizon_days - 1) + 1
            ):
                candidate = Assignment(
                    work_order_id=order.id, technician_id=tech.id, day=day
                )
                trial = Plan(assignments=tuple(selected + [candidate]))
                if evaluate_plan(scenario, trial).feasible:
                    candidates.append(
                        (risk_value_cents(scenario, order, day), candidate)
                    )
        if candidates:
            selected.append(
                max(candidates, key=lambda x: (x[0], -x[1].day, x[1].technician_id))[1]
            )
    return Plan(assignments=tuple(selected))
