from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.greedy import greedy_plan
from risk_resource.domain.models import Scenario, SolveResult


class Solver(Protocol):
    """Solver port. Conformance: tests/test_solver_contract.py.

    A returned evaluation must equal the shared checker's verdict on the plan,
    the result must be deterministic for a fixed input, and proven_infeasible
    may be set only when no feasible plan exists.
    """

    def solve(self, scenario: Scenario, *, max_seconds: float = 10.0) -> SolveResult: ...


class GreedySolver:
    """Frozen heuristic baseline behind the same port as CP-SAT."""

    def solve(self, scenario: Scenario, *, max_seconds: float = 10.0) -> SolveResult:
        plan = greedy_plan(scenario)
        return SolveResult(
            plan=plan, evaluation=evaluate_plan(scenario, plan), solver="greedy"
        )


@dataclass(frozen=True)
class SolveService:
    solver: Solver = CpSatSolver()  # noqa: RUF009 - stateless default

    def baseline(self, scenario: Scenario) -> SolveResult:
        return GreedySolver().solve(scenario)

    def optimized(self, scenario: Scenario, max_seconds: float = 10) -> SolveResult:
        return self.solver.solve(scenario, max_seconds=max_seconds)


def scenario_hash(scenario: Scenario) -> str:
    return sha256(
        json.dumps(
            scenario.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
