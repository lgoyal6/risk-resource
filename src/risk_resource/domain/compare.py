from __future__ import annotations

from risk_resource.adapters.cp_sat import CpSatSolver

from .checker import evaluate_plan
from .greedy import greedy_plan
from .models import Scenario


def compare(scenario: Scenario):
    baseline = evaluate_plan(scenario, greedy_plan(scenario))
    optimized = CpSatSolver().solve(scenario).evaluation
    return {
        "baseline": baseline,
        "optimized": optimized,
        "objective_delta_cents": optimized.objective_cents - baseline.objective_cents,
    }


def sensitivity(scenario: Scenario, risk_weights: tuple[float, ...] = (0.5, 1.0, 2.0)):
    return [
        {
            "risk_weight": weight,
            "objective_cents": CpSatSolver()
            .solve(scenario.model_copy(update={"risk_weight": weight}))
            .evaluation.objective_cents,
        }
        for weight in risk_weights
    ]
