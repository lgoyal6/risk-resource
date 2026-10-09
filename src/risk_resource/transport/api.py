import json
import os
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.adapters.postgres import PostgresRepository
from risk_resource.application.services import SolveService, scenario_hash
from risk_resource.domain.compare import compare, sensitivity
from risk_resource.domain.decisions import (
    DecisionEntry,
    append_decision,
    verify_decisions,
)
from risk_resource.domain.models import Plan, Scenario
from risk_resource.sample import demo_scenario
from risk_resource.transport.auth import Principal, principal, require_role


class DecisionRequest(BaseModel):
    action: Literal["approve", "override", "reject", "rollback"]
    recommendation_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=4096)
    plan: Plan | None = None
    actor: str | None = (
        None  # Legacy clients may send this; it never selects the actor.
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Risk Resource", version="0.1.0")

    @app.middleware("http")
    async def request_size_limit(request, call_next):
        content_length = request.headers.get("content-length")
        from fastapi.responses import JSONResponse

        try:
            declared = int(content_length) if content_length else 0
        except ValueError:
            return JSONResponse({"detail": "invalid Content-Length"}, status_code=400)
        if declared < 0:
            return JSONResponse({"detail": "invalid Content-Length"}, status_code=400)
        maximum = 2 * 1024 * 1024
        if declared > maximum:
            return JSONResponse(
                {"detail": "request body exceeds 2 MiB"}, status_code=413
            )
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > maximum:
                return JSONResponse(
                    {"detail": "request body exceeds 2 MiB"}, status_code=413
                )
            body.extend(chunk)
        # Starlette's wrapped receive replays cached bytes to the route parser.
        request._body = bytes(body)
        return await call_next(request)

    ui = Path(__file__).parent / "static" / "index.html"

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ui)

    service = SolveService(CpSatSolver())
    scenarios: dict[str, Scenario] = {"demo-week": demo_scenario()}
    scenario_teams: dict[str, str] = {"demo-week": "demo"}
    decisions: dict[str, list[DecisionEntry]] = {}
    decision_keys: dict[tuple[str, str, str], tuple[str, DecisionEntry]] = {}
    lock = RLock()
    database = os.getenv("DATABASE_URL")
    persistent = None
    if database:
        persistent = PostgresRepository(database, service)
        persistent.open()
        persistent.migrate()
        if persistent.get_scenario("demo-week", "demo") is None:
            persistent.save_scenario(demo_scenario(), "demo")

    def owned(scenario_id: str, p: Principal) -> Scenario:
        if persistent:
            scenario = persistent.get_scenario(scenario_id, p.team)
            if scenario is None: raise HTTPException(404, "scenario not found")
            return scenario
        scenario = scenarios.get(scenario_id)
        if scenario is None or scenario_teams.get(scenario_id) != p.team:
            raise HTTPException(404, "scenario not found")
        return scenario

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/scenarios")
    def list_scenarios(p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        if persistent:
            return [{"id": s.id, "name": s.name, "provenance": s.provenance} for s in persistent.list_scenarios(p.team)]
        with lock:
            return [
                {"id": s.id, "name": s.name, "provenance": s.provenance}
                for sid, s in scenarios.items()
                if scenario_teams[sid] == p.team
            ]

    @app.post("/scenarios", status_code=201)
    def create_scenario(scenario: Scenario, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "planner")
        if persistent:
            if persistent.get_scenario(scenario.id, p.team) is not None: raise HTTPException(409, "scenario already exists")
            persistent.save_scenario(scenario, p.team)
            return {"id": scenario.id, "sha256": scenario_hash(scenario)}
        with lock:
            if scenario.id in scenarios:
                raise HTTPException(409, "scenario already exists")
            scenarios[scenario.id] = scenario
            scenario_teams[scenario.id] = p.team
        return {"id": scenario.id, "sha256": scenario_hash(scenario)}

    @app.get("/scenarios/{scenario_id}")
    def get_scenario(scenario_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        return owned(scenario_id, p)

    @app.post("/scenarios/{scenario_id}/baseline")
    def baseline(scenario_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        return service.baseline(owned(scenario_id, p))

    @app.get("/scenarios/{scenario_id}/compare")
    def compare_scenario(scenario_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        return compare(owned(scenario_id, p))

    @app.get("/scenarios/{scenario_id}/sensitivity")
    def sensitivity_scenario(scenario_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        return sensitivity(owned(scenario_id, p))

    @app.get("/scenarios/{scenario_id}/decisions")
    def decision_history(scenario_id: str, p: Principal = Depends(principal)):
        require_role(p, "viewer")
        owned(scenario_id, p)
        entries = persistent.decisions(scenario_id, p.team) if persistent else decisions.get(scenario_id, [])
        return list(entries)

    @app.get("/scenarios/{scenario_id}/decisions/verify")
    def verify(scenario_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        owned(scenario_id, p)
        entries = persistent.decisions(scenario_id, p.team) if persistent else decisions.get(scenario_id, [])
        return {"valid": verify_decisions(tuple(entries))[0]}

    @app.post("/scenarios/{scenario_id}/decisions", status_code=201)
    def decide(
        scenario_id: str,
        entry: DecisionRequest,
        response: Response,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        p: Principal = Depends(principal),  # noqa: B008
    ):
        require_role(p, "approver")
        scenario = owned(scenario_id, p)
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(400, "Idempotency-Key must be 1..128 characters")
        key = (p.team, scenario_id, idempotency_key)
        if persistent and entry.action != "reject":
            try:
                recommendation = persistent.recommendation(entry.recommendation_id, scenario_id, p.team)
            except ValueError:
                recommendation = None
            if recommendation is None:
                raise HTTPException(422, "recommendation must be a persisted result for this scenario")
        payload = {**entry.model_dump(mode="json", exclude={"actor"}), "actor": p.user}
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        # The cache claim and hash-chain append must serialize competing retries.
        with lock:
            if key in decision_keys:
                prior_hash, prior = decision_keys[key]
                if prior_hash != digest:
                    raise HTTPException(
                        409, "idempotency key reused for a different decision"
                    )
                response.status_code = 200
                return prior
            try:
                existing = persistent.decisions(scenario_id, p.team) if persistent else decisions.get(scenario_id, [])
                created = append_decision(
                    tuple(existing),
                    action=entry.action,
                    recommendation_id=entry.recommendation_id,
                    actor=p.user,
                    reason=entry.reason,
                    plan=entry.plan,
                    scenario=scenario,
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if persistent:
                try:
                    persistent.append_decision(scenario, p.team, created)
                except ValueError as exc:
                    raise HTTPException(409, str(exc)) from exc
            else:
                decisions.setdefault(scenario_id, []).append(created)
                decision_keys[key] = (digest, created)
            return created

    @app.get("/scenarios/{scenario_id}/jobs/{job_id}")
    def job_status(scenario_id: str, job_id: str, p: Principal = Depends(principal)):
        require_role(p, "viewer")
        owned(scenario_id, p)
        if not persistent:
            raise HTTPException(404, "persistent jobs are disabled")
        job = persistent.job(job_id, scenario_id, p.team)
        if job is None: raise HTTPException(404, "job not found")
        recommendation = persistent.recommendation(str(job["id"]), scenario_id, p.team) if job["status"] == "succeeded" else None
        return {"job": job, "recommendation": recommendation}

    @app.post("/scenarios/{scenario_id}/jobs/{job_id}/cancel")
    def cancel_job(scenario_id: str, job_id: str, p: Principal = Depends(principal)):
        require_role(p, "planner")
        owned(scenario_id, p)
        if not persistent: raise HTTPException(404, "persistent jobs are disabled")
        return {"cancelled": persistent.cancel_job(__import__("uuid").UUID(job_id), p.team)}

    @app.post("/scenarios/{scenario_id}/solve")
    def solve(scenario_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "planner")
        scenario = owned(scenario_id, p)
        if persistent:
            job = persistent.enqueue_job(scenario.id, p.team, scenario_hash(scenario))
            return {"status": "queued", "job_id": str(job), "scenario_id": scenario.id}
        result = service.optimized(scenario)
        if not result.evaluation.feasible:
            raise HTTPException(
                422,
                detail={
                    "message": "recommendation is infeasible",
                    "violations": result.evaluation.violations,
                },
            )
        return result

    if persistent:
        @app.on_event("shutdown")
        def close_database():
            persistent.close()
    return app


app = create_app()
