"""Conformance suite for the Solver port (application.services.Solver).

Every adapter must satisfy the same guarantees; the shared checker is the oracle.
"""

import pytest

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.application.services import GreedySolver
from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.models import Scenario
from risk_resource.sample import demo_scenario
from risk_resource.synthetic import INFEASIBILITY_KINDS, fleet_scenario

ADAPTERS = [GreedySolver(), CpSatSolver()]
IDS = ["greedy", "cp-sat"]


def _with(scenario: Scenario, **orders) -> Scenario:
    data = scenario.model_dump(mode="json")
    for w in data["work_orders"]:
        w.update(orders.get(w["id"], {}))
    return Scenario.model_validate(data)


@pytest.mark.parametrize("solver", ADAPTERS, ids=IDS)
@pytest.mark.parametrize("seed", [10000, 10001, 10002, 10003, 10004, 10005])
def test_reported_evaluation_is_the_checker_verdict(solver, seed):
    scenario = fleet_scenario(seed)
    result = solver.solve(scenario, max_seconds=2)
    assert result.evaluation == evaluate_plan(scenario, result.plan)


@pytest.mark.parametrize("solver", ADAPTERS, ids=IDS)
def test_deterministic_for_fixed_input(solver):
    scenario = fleet_scenario(10003)
    assert solver.solve(scenario, max_seconds=2) == solver.solve(scenario, max_seconds=2)


@pytest.mark.parametrize("solver", ADAPTERS, ids=IDS)
@pytest.mark.parametrize("kind", INFEASIBILITY_KINDS)
def test_infeasible_mandatory_work_is_never_reported_feasible(solver, kind):
    scenario = fleet_scenario(20000, infeasible=kind)
    result = solver.solve(scenario, max_seconds=2)
    assert not result.evaluation.feasible
    assert not evaluate_plan(scenario, result.plan).feasible


def test_only_cp_sat_proves_infeasibility():
    scenario = fleet_scenario(20000, infeasible="missing_skill")
    assert CpSatSolver().solve(scenario, max_seconds=2).proven_infeasible
    assert not GreedySolver().solve(scenario).proven_infeasible  # unsupported: heuristic


@pytest.mark.parametrize("solver", ADAPTERS, ids=IDS)
def test_two_mandatory_orders_are_both_scheduled(solver):
    # Regression: the as-found greedy rejected every partial plan once two
    # orders were mandatory and returned an empty plan.
    scenario = _with(demo_scenario(), **{"wo-02": {"mandatory": True}})
    result = solver.solve(scenario, max_seconds=2)
    assert result.evaluation.feasible
    assert {"wo-01", "wo-02"} <= {a.work_order_id for a in result.plan.assignments}


@pytest.mark.parametrize("solver", ADAPTERS, ids=IDS)
def test_precedence_is_respected(solver):
    # Regression: the as-found CP-SAT model had no precedence constraint, so
    # it could return a plan the checker rejects.
    scenario = _with(
        demo_scenario(),
        **{"wo-01": {"after": ["wo-03"], "earliest_day": 0, "latest_day": 3, "due_day": 3}},
    )
    result = solver.solve(scenario, max_seconds=2)
    assert result.evaluation.feasible
    days = {a.work_order_id: a.day for a in result.plan.assignments}
    assert days["wo-03"] < days["wo-01"]


@pytest.mark.parametrize("solver", ADAPTERS, ids=IDS)
def test_cluster_without_drain_limit_stays_online(solver):
    # The checker treats a cluster missing from max_offline_per_day as limit 0.
    base = demo_scenario()
    data = base.model_dump(mode="json")
    data["max_offline_per_day"].pop("train-b", None)
    for w in data["work_orders"]:
        w["mandatory"] = False
    scenario = Scenario.model_validate(data)
    result = solver.solve(scenario, max_seconds=2)
    assert result.evaluation.feasible
    clusters = {a.id: a.cluster for a in scenario.assets}
    orders = {w.id: w.asset_id for w in scenario.work_orders}
    assert all(clusters[orders[a.work_order_id]] != "train-b" for a in result.plan.assignments)


def test_cp_sat_bound_covers_the_checker_objective():
    for seed in (10000, 10001, 10002):
        scenario = fleet_scenario(seed)
        result = CpSatSolver().solve(scenario, max_seconds=2)
        if result.evaluation.feasible:
            assert result.objective_bound_cents is not None
            assert result.objective_bound_cents >= result.evaluation.objective_cents
            greedy = GreedySolver().solve(scenario)
            if greedy.evaluation.feasible:
                assert result.evaluation.objective_cents >= greedy.evaluation.objective_cents
