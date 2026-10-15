import json
import logging
import os
import re
import time
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field

from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.adapters.postgres import PostgresRepository
from risk_resource.application.approvals import (
    DigestMismatch,
    StaleScenario,
    bound_decision,
)
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
    # Required in durable mode: the version this decision was made against (ADR 0008).
    scenario_version: int | None = Field(default=None, ge=1)


class RevisionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    scenario: Scenario


log = logging.getLogger("risk_resource.api")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def solve_request_hash(scenario_id: str, version: int, content: str, key: str | None) -> str:
    """Job identity: same pinned input and same key means the same job."""
    return sha256(
        json.dumps(
            [scenario_id, version, content, key or "default"], separators=(",", ":")
        ).encode()
    ).hexdigest()


def create_app() -> FastAPI:
    app = FastAPI(title="Risk Resource", version="0.1.0")
    registry = CollectorRegistry()
    # Labels are bounded: route templates, methods, status codes and action names only.
    http_requests = Counter(
        "rr_http_requests", "HTTP requests", ["method", "route", "status"], registry=registry
    )
    http_latency = Histogram(
        "rr_http_request_seconds", "HTTP latency", ["route"], registry=registry
    )
    decision_outcomes = Counter(
        "rr_decisions", "Decision attempts", ["action", "outcome"], registry=registry
    )
    solve_requests = Counter(
        "rr_solve_requests", "Solve requests", ["outcome"], registry=registry
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _REQUEST_ID.match(supplied) else uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        elapsed = time.perf_counter() - started
        http_requests.labels(request.method, route, str(response.status_code)).inc()
        http_latency.labels(route).observe(elapsed)
        response.headers["X-Request-ID"] = request_id
        # Never log headers or bodies: API keys and plans stay out of logs.
        log.info(
            "request_id=%s method=%s route=%s status=%s ms=%.1f",
            request_id, request.method, route, response.status_code, elapsed * 1000,
        )
        return response

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

    @app.get("/ready")
    def ready():
        if persistent:
            try:
                with persistent.pool.connection(timeout=2) as conn:
                    conn.execute("SELECT 1")
            except Exception as exc:
                raise HTTPException(503, f"database unavailable: {type(exc).__name__}") from exc
        return {"status": "ready", "durable": bool(persistent)}

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return PlainTextResponse(generate_latest(registry), media_type="text/plain; version=0.0.4")

    @app.get("/scenarios")
    def list_scenarios(p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        if persistent:
            out = []
            for s in persistent.list_scenarios(p.team):
                current = persistent.head(s.id, p.team)
                out.append({"id": s.id, "name": s.name, "provenance": s.provenance, "version": current[1] if current else None})
            return out
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
            try:
                persistent.save_scenario(scenario, p.team, p.user)
            except Exception as exc:  # another team owns the id; do not reveal it
                if type(exc).__name__ != "UniqueViolation":
                    raise
                raise HTTPException(409, "scenario already exists") from exc
            return {"id": scenario.id, "sha256": scenario_hash(scenario), "version": 1}
        with lock:
            if scenario.id in scenarios:
                raise HTTPException(409, "scenario already exists")
            scenarios[scenario.id] = scenario
            scenario_teams[scenario.id] = p.team
        return {"id": scenario.id, "sha256": scenario_hash(scenario)}

    @app.post("/scenarios/{scenario_id}/versions", status_code=201)
    def revise_scenario(
        scenario_id: str,
        body: RevisionRequest,
        p: Principal = Depends(principal),  # noqa: B008
    ):
        require_role(p, "planner")
        if not persistent:
            raise HTTPException(404, "scenario versions require DATABASE_URL")
        owned(scenario_id, p)
        if body.scenario.id != scenario_id:
            raise HTTPException(422, "scenario id does not match the path")
        try:
            version = persistent.revise_scenario(body.scenario, p.team, body.expected_version, p.user)
        except StaleScenario as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": scenario_id, "version": version, "sha256": scenario_hash(body.scenario)}

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
    def decision_history(scenario_id: str, p: Principal = Depends(principal)):  # noqa: B008
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
        payload = {**entry.model_dump(mode="json", exclude={"actor"}), "actor": p.user}
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if persistent:
            return durable_decision(scenario_id, entry, response, idempotency_key, digest, p)
        key = (p.team, scenario_id, idempotency_key)
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
                created = append_decision(
                    tuple(decisions.get(scenario_id, [])),
                    action=entry.action,
                    recommendation_id=entry.recommendation_id,
                    actor=p.user,
                    reason=entry.reason,
                    plan=entry.plan,
                    scenario=scenario,
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            decisions.setdefault(scenario_id, []).append(created)
            decision_keys[key] = (digest, created)
            return created

    def durable_decision(scenario_id, entry, response, idempotency_key, digest, p):
        """Version- and digest-bound decision; every read and write goes to PostgreSQL."""
        assert persistent is not None

        def outcome(label):
            decision_outcomes.labels(entry.action, label).inc()

        try:
            prior = persistent.replay_decision(scenario_id, p.team, idempotency_key, digest)
        except ValueError as exc:
            outcome("conflict")
            raise HTTPException(409, str(exc)) from exc
        if prior is not None:
            outcome("replayed")
            response.status_code = 200
            return prior
        if entry.scenario_version is None:
            outcome("invalid")
            raise HTTPException(422, "scenario_version is required")
        current = persistent.head(scenario_id, p.team)
        if current is None:
            raise HTTPException(404, "scenario not found")
        head, version = current
        recommendation = (
            persistent.bound_recommendation(entry.recommendation_id, scenario_id, p.team)
            if entry.action != "reject"
            else None
        )
        if entry.action != "reject" and recommendation is None:
            outcome("invalid")
            raise HTTPException(422, "recommendation must be a persisted result for this scenario")
        try:
            created = bound_decision(
                tuple(persistent.decisions(scenario_id, p.team)),
                action=entry.action,
                actor=p.user,
                reason=entry.reason,
                plan=entry.plan,
                requested_version=entry.scenario_version,
                current_version=version,
                current=head,
                recommendation=recommendation,
                recommendation_id=entry.recommendation_id,
            )
        except StaleScenario as exc:
            outcome("stale")
            raise HTTPException(409, {"code": "stale_scenario", "message": str(exc)}) from exc
        except DigestMismatch as exc:
            outcome("invalid")
            raise HTTPException(422, {"code": "digest_mismatch", "message": str(exc)}) from exc
        except ValueError as exc:
            outcome("invalid")
            raise HTTPException(422, str(exc)) from exc
        try:
            with lock:
                created, was_created = persistent.append_decision(
                    head, p.team, created, idempotency_key, digest
                )
        except StaleScenario as exc:
            outcome("stale")
            raise HTTPException(409, {"code": "stale_scenario", "message": str(exc)}) from exc
        except ValueError as exc:
            outcome("conflict")
            raise HTTPException(409, str(exc)) from exc
        outcome("created" if was_created else "replayed")
        response.status_code = 201 if was_created else 200
        return created

    @app.get("/scenarios/{scenario_id}/jobs/{job_id}")
    def job_status(scenario_id: str, job_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "viewer")
        owned(scenario_id, p)
        if not persistent:
            raise HTTPException(404, "persistent jobs are disabled")
        try:
            UUID(job_id)
        except ValueError as exc:
            raise HTTPException(404, "job not found") from exc
        job = persistent.job(job_id, scenario_id, p.team)
        if job is None: raise HTTPException(404, "job not found")
        recommendation = persistent.recommendation_for_job(str(job["id"]), scenario_id, p.team) if job["status"] == "succeeded" else None
        return {"job": job, "recommendation": recommendation}

    @app.post("/scenarios/{scenario_id}/jobs/{job_id}/cancel")
    def cancel_job(scenario_id: str, job_id: str, p: Principal = Depends(principal)):  # noqa: B008
        require_role(p, "planner")
        owned(scenario_id, p)
        if not persistent: raise HTTPException(404, "persistent jobs are disabled")
        try:
            parsed = UUID(job_id)
        except ValueError as exc:
            raise HTTPException(404, "job not found") from exc
        return {"cancelled": persistent.cancel_job(parsed, p.team)}

    @app.post("/scenarios/{scenario_id}/solve")
    def solve(
        scenario_id: str,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        p: Principal = Depends(principal),  # noqa: B008
    ):
        require_role(p, "planner")
        scenario = owned(scenario_id, p)
        if persistent:
            if idempotency_key is not None and not 1 <= len(idempotency_key) <= 128:
                raise HTTPException(400, "Idempotency-Key must be 1..128 characters")
            current = persistent.head(scenario_id, p.team)
            if current is None:
                raise HTTPException(404, "scenario not found")
            head, version = current
            request_hash = solve_request_hash(scenario_id, version, scenario_hash(head), idempotency_key)
            try:
                job = persistent.enqueue_job(scenario_id, p.team, request_hash, scenario_version=version)
            except ValueError as exc:
                solve_requests.labels("conflict").inc()
                raise HTTPException(409, str(exc)) from exc
            solve_requests.labels("queued").inc()
            return {"status": "queued", "job_id": str(job), "scenario_id": scenario_id, "scenario_version": version}
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
