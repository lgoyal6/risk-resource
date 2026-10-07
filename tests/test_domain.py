from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.greedy import greedy_plan
from risk_resource.sample import demo_scenario


def test_greedy_is_feasible_and_deterministic():
    s = demo_scenario()
    a = greedy_plan(s)
    assert evaluate_plan(s, a).feasible
    assert a == greedy_plan(s)


def test_cp_sat_recommendation_is_feasible():
    s = demo_scenario()
    result = CpSatSolver().solve(s)
    assert result.evaluation.feasible


def test_duplicate_assignment_rejected():
    s = demo_scenario()
    plan = greedy_plan(s)
    if plan.assignments:
        from risk_resource.domain.models import Plan

        bad = Plan(assignments=plan.assignments + (plan.assignments[0],))
        assert not evaluate_plan(s, bad).feasible


def test_decision_chain_detects_tampering():
    from risk_resource.domain.decisions import append_decision, verify_decisions

    s = demo_scenario()
    plan = greedy_plan(s)
    first = append_decision(
        (),
        action="approve",
        recommendation_id="rec-1",
        actor="ops",
        reason="",
        plan=plan,
        scenario=s,
    )
    second = append_decision(
        (first,),
        action="override",
        recommendation_id="rec-1",
        actor="ops",
        reason="Keep local technician",
        plan=plan,
        scenario=s,
    )
    assert verify_decisions((first, second)) == (True, None)
    from dataclasses import replace

    tampered = replace(second, reason="tampered")
    assert verify_decisions((first, tampered)) == (False, 1)
