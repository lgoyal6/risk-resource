from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.greedy import greedy_plan
from risk_resource.domain.models import Scenario, SolveResult


@dataclass(frozen=True)
class SolveService:
    solver: CpSatSolver

    def baseline(self, scenario: Scenario) -> SolveResult:
        plan = greedy_plan(scenario)
        return SolveResult(
            plan=plan, evaluation=evaluate_plan(scenario, plan), solver="greedy"
        )

    def optimized(self, scenario: Scenario, max_seconds: float = 10) -> SolveResult:
        return self.solver.solve(scenario, max_seconds=max_seconds)


def scenario_hash(scenario: Scenario) -> str:
    return sha256(
        json.dumps(
            scenario.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
