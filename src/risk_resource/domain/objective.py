from __future__ import annotations

from .models import Scenario, WorkOrder


def risk_value_cents(scenario: Scenario, order: WorkOrder, day: int) -> int:
    asset = next(a for a in scenario.assets if a.id == order.asset_id)
    probability = (order.failure_probability_low + order.failure_probability_high) / 2
    remaining = max(0, order.risk_window_days - day)
    dollars = (
        probability
        * asset.downtime_cost_usd
        * order.risk_reduction
        * remaining
        / order.risk_window_days
    )
    return round(dollars * 100 * scenario.risk_weight)


def travel_cost_cents(scenario: Scenario, technician_id: str, order_id: str) -> int:
    technician = next(t for t in scenario.technicians if t.id == technician_id)
    order = next(w for w in scenario.work_orders if w.id == order_id)
    asset = next(a for a in scenario.assets if a.id == order.asset_id)
    return round(
        (0 if technician.home_site == asset.site else 25) * 100 * scenario.travel_weight
    )


def evaluate_objective(
    scenario: Scenario, assignments: tuple
) -> tuple[int, int, int, int]:
    by_order = {w.id: w for w in scenario.work_orders}
    risk = sum(
        risk_value_cents(scenario, by_order[a.work_order_id], a.day)
        for a in assignments
    )
    travel = sum(
        travel_cost_cents(scenario, a.technician_id, a.work_order_id)
        for a in assignments
    )
    done = {a.work_order_id: a.day for a in assignments}
    overdue = sum(
        round(by_order[w.id].overdue_penalty_usd * 100 * scenario.overdue_weight)
        for w in scenario.work_orders
        if w.id not in done or done[w.id] > w.due_day
    )
    return risk - travel - overdue, risk, travel, overdue
