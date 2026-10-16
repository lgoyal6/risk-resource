"""Stateless public demo API for the bundled maintenance scenario.

It calls the existing greedy and CP-SAT solvers without loading the private app's
database, user-authentication, or approval routes.
"""
from fastapi import FastAPI

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.application.services import GreedySolver
from risk_resource.sample import demo_scenario

app = FastAPI(
    title="Risk Resource public demo",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/api/demo")
def run_demo() -> dict:
    """Compare both solvers on one fixed synthetic fleet, without persistent writes."""
    scenario = demo_scenario()
    return {
        "scenario": scenario.model_dump(mode="json"),
        "baseline": GreedySolver().solve(scenario).model_dump(mode="json"),
        "optimized": CpSatSolver().solve(scenario, max_seconds=3).model_dump(mode="json"),
        "evidence_boundary": "Synthetic demo-week fixture. No fleet data, account, database, or external service is used.",
    }
