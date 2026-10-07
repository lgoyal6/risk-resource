from fastapi import FastAPI, HTTPException

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.application.services import SolveService, scenario_hash
from risk_resource.domain.compare import compare, sensitivity
from risk_resource.domain.decisions import (
    DecisionEntry,
    append_decision,
    verify_decisions,
)
from risk_resource.domain.models import Plan, Scenario
from risk_resource.sample import demo_scenario


def create_app() -> FastAPI:
    app = FastAPI(title="Risk Resource", version="0.1.0")
    service = SolveService(CpSatSolver())
    scenarios: dict[str, Scenario] = {"demo-week": demo_scenario()}
    decisions: dict[str, list[DecisionEntry]] = {}

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/scenarios")
    def list_scenarios():
        return [
            {"id": s.id, "name": s.name, "provenance": s.provenance}
            for s in scenarios.values()
        ]

    @app.post("/scenarios", status_code=201)
    def create_scenario(scenario: Scenario):
        if scenario.id in scenarios:
            raise HTTPException(409, "scenario already exists")
        scenarios[scenario.id] = scenario
        return {"id": scenario.id, "sha256": scenario_hash(scenario)}

    @app.get("/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str):
        if scenario_id not in scenarios:
            raise HTTPException(404, "scenario not found")
        return scenarios[scenario_id]

    @app.post("/scenarios/{scenario_id}/baseline")
    def baseline(scenario_id: str):
        if scenario_id not in scenarios:
            raise HTTPException(404, "scenario not found")
        return service.baseline(scenarios[scenario_id])

    @app.get("/scenarios/{scenario_id}/compare")
    def compare_scenario(scenario_id: str):
        if scenario_id not in scenarios:
            raise HTTPException(404, "scenario not found")
        return compare(scenarios[scenario_id])

    @app.get("/scenarios/{scenario_id}/sensitivity")
    def sensitivity_scenario(scenario_id: str):
        if scenario_id not in scenarios:
            raise HTTPException(404, "scenario not found")
        return sensitivity(scenarios[scenario_id])

    @app.get("/scenarios/{scenario_id}/decisions/verify")
    def verify(scenario_id: str):
        if scenario_id not in scenarios:
            raise HTTPException(404, "scenario not found")
        return {"valid": verify_decisions(tuple(decisions.get(scenario_id, [])))[0]}

    @app.post("/scenarios/{scenario_id}/decisions", status_code=201)
    def decide(scenario_id: str, entry: dict):
        if scenario_id not in scenarios:
            raise HTTPException(404, "scenario not found")
        plan = Plan.model_validate(entry["plan"]) if entry.get("plan") else None
        current = tuple(decisions.get(scenario_id, []))
        try:
            created = append_decision(
                current,
                action=entry["action"],
                recommendation_id=entry["recommendation_id"],
                actor=entry["actor"],
                reason=entry.get("reason", ""),
                plan=plan,
                scenario=scenarios[scenario_id],
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        decisions.setdefault(scenario_id, []).append(created)
        return created

    @app.post("/scenarios/{scenario_id}/solve")
    def solve(scenario_id: str):
        if scenario_id not in scenarios:
            raise HTTPException(404, "scenario not found")
        result = service.optimized(scenarios[scenario_id])
        if not result.evaluation.feasible:
            raise HTTPException(
                422,
                detail={
                    "message": "recommendation is infeasible",
                    "violations": result.evaluation.violations,
                },
            )
        return result

    return app


app = create_app()
