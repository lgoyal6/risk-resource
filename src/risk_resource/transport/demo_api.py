"""Stateless public demo API for the bundled maintenance scenario.

It calls the existing greedy and CP-SAT solvers without loading the private app's
database, user-authentication, or approval routes.
"""
from typing import Annotated

from fastapi import FastAPI, Query

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.application.services import GreedySolver
from risk_resource.sample import demo_scenario
from risk_resource.domain.models import Skill

app = FastAPI(
    title="Risk Resource public demo",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/api/demo")
def run_demo(
    technician_minutes: Annotated[int | None, Query(ge=30, le=480)] = None,
    firmware_skill: bool = True,
    max_offline: Annotated[int, Query(ge=0, le=3)] = 1,
) -> dict:
    """Compare existing solvers on bounded edits to the synthetic fleet."""
    scenario = demo_scenario()
    scenario = scenario.model_copy(update={
        "technicians": tuple(technician.model_copy(update={
            "available_minutes_per_day": technician_minutes or technician.available_minutes_per_day,
            "skills": technician.skills if firmware_skill else technician.skills - {Skill.FIRMWARE},
        }) for technician in scenario.technicians),
        "max_offline_per_day": {cluster: max_offline for cluster in scenario.max_offline_per_day},
    })
    return {
        "scenario": scenario.model_dump(mode="json"),
        "baseline": GreedySolver().solve(scenario).model_dump(mode="json"),
        "optimized": CpSatSolver().solve(scenario, max_seconds=3).model_dump(mode="json"),
        "evidence_boundary": "Synthetic demo-week fixture with visitor-selected constraints. No fleet data, account, database, or external service is used.",
    }
