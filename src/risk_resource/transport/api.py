from fastapi import FastAPI, HTTPException

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.application.services import SolveService, scenario_hash
from risk_resource.domain.models import Scenario
from risk_resource.sample import demo_scenario


def create_app() -> FastAPI:
    app = FastAPI(title="Risk Resource", version="0.1.0")
    service = SolveService(CpSatSolver())
    scenarios: dict[str, Scenario] = {"demo-week": demo_scenario()}

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
