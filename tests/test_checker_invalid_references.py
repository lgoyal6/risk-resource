"""Reject invalid plan references through the independent feasibility checker.
The API and PostgreSQL worker use checker.py before accepting a proposed plan.
Unknown IDs must produce a verdict rather than crash the validation boundary.
"""
import pytest

from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.models import Assignment, Plan
from risk_resource.sample import demo_scenario


@pytest.mark.parametrize("unknown", ["work_order", "technician"])
def test_checker_rejects_unknown_assignment_ids(unknown):
    scenario = demo_scenario()
    plan = Plan(assignments=(Assignment(
        work_order_id="unknown" if unknown == "work_order" else scenario.work_orders[0].id,
        technician_id="unknown" if unknown == "technician" else scenario.technicians[0].id,
        day=0,
    ),))
    result = evaluate_plan(scenario, plan)
    assert not result.feasible
    assert any("unknown" in violation for violation in result.violations)
