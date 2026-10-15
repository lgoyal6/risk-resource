from __future__ import annotations

from .checker import evaluate_plan
from .models import Assignment, Plan, Scenario, WorkOrder
from .objective import risk_value_cents


def _priority_order(scenario: Scenario) -> list[WorkOrder]:
    """Mandatory first, then highest risk, with every predecessor placed earlier."""
    ranked = sorted(
        scenario.work_orders,
        key=lambda w: (
            not w.mandatory,
            -risk_value_cents(scenario, w, w.earliest_day),
            w.id,
        ),
    )
    by_id = {w.id: w for w in scenario.work_orders}
    placed: set[str] = set()
    ordered: list[WorkOrder] = []

    def place(order: WorkOrder, visiting: frozenset[str]) -> None:
        if order.id in placed or order.id in visiting:
            return
        for predecessor in order.after:
            place(by_id[predecessor], visiting | {order.id})
        placed.add(order.id)
        ordered.append(order)

    for order in ranked:
        place(order, frozenset())
    return ordered


def greedy_plan(scenario: Scenario) -> Plan:
    selected: list[Assignment] = []
    for order in _priority_order(scenario):
        candidates = []
        for tech in scenario.technicians:
            for day in range(
                order.earliest_day, min(order.latest_day, scenario.horizon_days - 1) + 1
            ):
                candidate = Assignment(
                    work_order_id=order.id, technician_id=tech.id, day=day
                )
                trial = Plan(assignments=tuple(selected + [candidate]))
                # Partial plans cannot cover every mandatory order yet; check the rest.
                if evaluate_plan(scenario, trial, require_mandatory=False).feasible:
                    candidates.append(
                        (risk_value_cents(scenario, order, day), candidate)
                    )
        if candidates:
            selected.append(
                max(candidates, key=lambda x: (x[0], -x[1].day, x[1].technician_id))[1]
            )
    return Plan(assignments=tuple(selected))
