"""Pure domain models for preventive GPU maintenance planning."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")]


class Provenance(StrEnum):
    DEMO = "demo"
    SYNTHETIC_BENCHMARK = "synthetic_benchmark"
    PILOT = "pilot"
    REAL = "real"


class Skill(StrEnum):
    GPU = "gpu"
    NETWORK = "network"
    FACILITIES = "facilities"
    FIRMWARE = "firmware"


class Asset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Id
    site: Id
    cluster: Id
    downtime_cost_usd: float = Field(ge=0)


class Technician(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Id
    home_site: Id
    skills: frozenset[Skill]
    available_minutes_per_day: int = Field(gt=0)
    cleared_sites: frozenset[Id]


class WorkOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Id
    asset_id: Id
    skill: Skill
    earliest_day: int = Field(ge=0)
    latest_day: int = Field(ge=0)
    due_day: int = Field(ge=0)
    p50_minutes: int = Field(gt=0)
    p90_minutes: int = Field(gt=0)
    failure_probability_low: float = Field(ge=0, le=1)
    failure_probability_high: float = Field(ge=0, le=1)
    risk_reduction: float = Field(ge=0, le=1)
    risk_window_days: int = Field(gt=0)
    mandatory: bool = False
    after: tuple[Id, ...] = ()
    overdue_penalty_usd: float = Field(ge=0)

    @model_validator(mode="after")
    def valid_ranges(self) -> WorkOrder:
        if self.latest_day < self.earliest_day or self.due_day < self.earliest_day:
            raise ValueError("work-order day ranges are invalid")
        if self.p90_minutes < self.p50_minutes:
            raise ValueError("p90_minutes must be >= p50_minutes")
        if self.failure_probability_high < self.failure_probability_low:
            raise ValueError("high failure probability must be >= low")
        return self


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Id
    name: str = Field(min_length=1, max_length=200)
    provenance: Provenance
    horizon_days: int = Field(gt=0, le=14)
    planning_quantile: Literal["p50", "p90"] = "p90"
    risk_weight: float = Field(default=1, ge=0)
    travel_weight: float = Field(default=1, ge=0)
    overdue_weight: float = Field(default=1, ge=0)
    max_offline_per_day: dict[Id, int]
    assets: tuple[Asset, ...]
    technicians: tuple[Technician, ...]
    work_orders: tuple[WorkOrder, ...]

    @model_validator(mode="after")
    def unique_and_references(self) -> Scenario:
        for values, label in (
            (self.assets, "asset"),
            (self.technicians, "technician"),
            (self.work_orders, "work order"),
        ):
            ids = [x.id for x in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {label} id")
        assets = {a.id: a for a in self.assets}
        orders = {w.id for w in self.work_orders}
        for w in self.work_orders:
            if w.asset_id not in assets:
                raise ValueError(f"unknown asset {w.asset_id}")
            missing = set(w.after) - orders
            if missing:
                raise ValueError(f"unknown predecessor(s): {sorted(missing)}")
        return self


class Assignment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_order_id: Id
    technician_id: Id
    day: int = Field(ge=0)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignments: tuple[Assignment, ...] = ()


class PlanEvaluation(BaseModel):
    feasible: bool
    objective_cents: int
    risk_value_cents: int
    travel_cost_cents: int
    overdue_penalty_cents: int
    violations: tuple[str, ...] = ()


class SolveResult(BaseModel):
    plan: Plan
    evaluation: PlanEvaluation
    solver: str
    deterministic: bool = True
    timed_out: bool = False
    explanation: tuple[str, ...] = ()
