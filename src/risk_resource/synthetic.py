"""Seeded synthetic fleet scenarios for benchmarks and tests.

Every scenario is labelled provenance=synthetic_benchmark and carries its seed in
the id. Infeasible cases are injected by construction; the label is the
generator's intent, and the study records the solver's proof separately.
"""

from __future__ import annotations

import random

from .domain.models import Asset, Provenance, Scenario, Skill, Technician, WorkOrder

INFEASIBILITY_KINDS = ("missing_skill", "capacity", "precedence_window", "drain_zero")


def fleet_scenario(
    seed: int,
    *,
    orders: int = 12,
    technicians: int = 4,
    sites: int = 2,
    horizon_days: int = 5,
    infeasible: str | None = None,
) -> Scenario:
    rng = random.Random(seed)
    site_ids = [f"site-{i}" for i in range(sites)]
    clusters = [f"cl-{i}" for i in range(sites * 2)]
    assets = tuple(
        Asset(
            id=f"gpu-{i:03d}",
            site=site_ids[i % sites],
            cluster=clusters[(i % sites) * 2 + rng.randrange(2)],
            downtime_cost_usd=rng.choice((4000, 7000, 9000, 12000, 20000)),
        )
        for i in range(max(3, orders * 2 // 3))
    )
    all_skills = list(Skill)
    techs = []
    for i in range(technicians):
        skills = set(rng.sample(all_skills, rng.randint(1, 3)))
        skills.add(all_skills[i % len(all_skills)])
        if infeasible == "missing_skill":
            skills.discard(Skill.FACILITIES)
            if not skills:
                skills.add(Skill.GPU)
        home = site_ids[i % sites]
        cleared = {home} | {s for s in site_ids if rng.random() < 0.5}
        techs.append(
            Technician(
                id=f"tech-{i}",
                home_site=home,
                skills=frozenset(skills),
                available_minutes_per_day=rng.choice((240, 360, 480)),
                cleared_sites=frozenset(cleared),
            )
        )
    available_skills = sorted({s for t in techs for s in t.skills})
    work: list[WorkOrder] = []
    for i in range(orders):
        asset = rng.choice(assets)
        earliest = rng.randrange(horizon_days)
        latest = rng.randrange(earliest, horizon_days)
        p50 = rng.choice((30, 60, 90, 120))
        low = round(rng.uniform(0.02, 0.3), 3)
        after: tuple[str, ...] = ()
        if i > 0 and rng.random() < 0.2:
            after = (f"wo-{rng.randrange(i):03d}",)
        work.append(
            WorkOrder(
                id=f"wo-{i:03d}",
                asset_id=asset.id,
                skill=rng.choice(available_skills),
                earliest_day=earliest,
                latest_day=latest,
                due_day=rng.randrange(earliest, horizon_days),
                p50_minutes=p50,
                p90_minutes=p50 + rng.choice((0, 15, 30, 60)),
                failure_probability_low=low,
                failure_probability_high=min(1.0, round(low + rng.uniform(0, 0.2), 3)),
                risk_reduction=round(rng.uniform(0.3, 0.9), 2),
                risk_window_days=rng.choice((14, 30, 60)),
                mandatory=rng.random() < 0.15,
                after=after,
                overdue_penalty_usd=rng.choice((0, 100, 500, 1500)),
            )
        )
    drain = {c: rng.randint(1, 3) for c in clusters}
    if infeasible == "missing_skill":
        work[0] = work[0].model_copy(update={"skill": Skill.FACILITIES, "mandatory": True})
    elif infeasible == "capacity":
        # Every mandatory minute exceeds what all technicians can supply in the window.
        work[0] = work[0].model_copy(
            update={
                "mandatory": True,
                "earliest_day": 0,
                "latest_day": 0,
                "p50_minutes": 2000,
                "p90_minutes": 2000,
            }
        )
    elif infeasible == "precedence_window":
        first = work[0].model_copy(
            update={"mandatory": True, "earliest_day": horizon_days - 1, "latest_day": horizon_days - 1, "due_day": horizon_days - 1}
        )
        second = work[1].model_copy(
            update={"mandatory": True, "earliest_day": 0, "latest_day": 0, "due_day": 0, "after": ("wo-000",)}
        )
        work[0], work[1] = first, second
    elif infeasible == "drain_zero":
        drain[next(a.cluster for a in assets if a.id == work[0].asset_id)] = 0
        work[0] = work[0].model_copy(update={"mandatory": True})
    elif infeasible is not None:
        raise ValueError(f"unknown infeasibility kind {infeasible}")
    return Scenario.model_validate(
        {
            "id": f"syn-{seed}" + (f"-{infeasible}" if infeasible else ""),
            "name": f"synthetic fleet seed {seed}",
            "provenance": Provenance.SYNTHETIC_BENCHMARK,
            "horizon_days": horizon_days,
            "planning_quantile": rng.choice(("p50", "p90")),
            "max_offline_per_day": drain,
            "assets": [a.model_dump(mode="json") for a in assets],
            "technicians": [t.model_dump(mode="json") for t in techs],
            "work_orders": [w.model_dump(mode="json") for w in work],
        }
    )
