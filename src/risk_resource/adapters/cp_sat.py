from __future__ import annotations

from ortools.sat.python import cp_model

from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.greedy import greedy_plan
from risk_resource.domain.models import Assignment, Plan, Scenario, SolveResult
from risk_resource.domain.objective import risk_value_cents, travel_cost_cents


class CpSatSolver:
    def solve(
        self, scenario: Scenario, *, max_seconds: float = 10.0, should_stop=None
    ) -> SolveResult:
        model = cp_model.CpModel()
        orders, techs = scenario.work_orders, scenario.technicians
        vars = {}
        for w in orders:
            for t in techs:
                asset = next(a for a in scenario.assets if a.id == w.asset_id)
                if w.skill not in t.skills or asset.site not in t.cleared_sites:
                    continue
                for day in range(
                    w.earliest_day, min(w.latest_day, scenario.horizon_days - 1) + 1
                ):
                    vars[(w.id, t.id, day)] = model.new_bool_var(
                        f"x_{w.id}_{t.id}_{day}"
                    )
        for w in orders:
            choices = [v for (wid, _, _), v in vars.items() if wid == w.id]
            (
                model.add(sum(choices) == 1)
                if w.mandatory
                else model.add(sum(choices) <= 1)
            )
        for t in techs:
            for day in range(scenario.horizon_days):
                model.add(
                    sum(
                        vars[(w.id, t.id, day)]
                        * (
                            w.p50_minutes
                            if scenario.planning_quantile == "p50"
                            else w.p90_minutes
                        )
                        for w in orders
                        if (w.id, t.id, day) in vars
                    )
                    <= t.available_minutes_per_day
                )
        for cluster, limit in scenario.max_offline_per_day.items():
            for day in range(scenario.horizon_days):
                for asset in scenario.assets:
                    relevant = [
                        vars[(w.id, t.id, day)]
                        for w in orders
                        if w.asset_id == asset.id
                        for t in techs
                        if (w.id, t.id, day) in vars
                    ]
                    if relevant:
                        model.add(sum(relevant) <= 1)
                model.add(
                    sum(
                        vars[(w.id, t.id, day)]
                        for w in orders
                        for t in techs
                        if (w.id, t.id, day) in vars
                        and next(
                            a for a in scenario.assets if a.id == w.asset_id
                        ).cluster
                        == cluster
                    )
                    <= limit
                )
        model.maximize(
            sum(
                v
                * (
                    risk_value_cents(
                        scenario, next(w for w in orders if w.id == wid), day
                    )
                    - travel_cost_cents(scenario, tid, wid)
                )
                for (wid, tid, day), v in vars.items()
            )
        )
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = max_seconds
        solver.parameters.num_workers = 1
        solver.parameters.random_seed = 7
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            baseline = greedy_plan(scenario)
            return SolveResult(
                plan=baseline,
                evaluation=evaluate_plan(scenario, baseline),
                solver="cp-sat",
                timed_out=status == cp_model.UNKNOWN,
                explanation=(
                    "No feasible CP-SAT solution; baseline shown for diagnosis.",
                ),
            )
        plan = Plan(
            assignments=tuple(
                Assignment(work_order_id=wid, technician_id=tid, day=day)
                for (wid, tid, day), v in vars.items()
                if solver.value(v)
            )
        )
        return SolveResult(
            plan=plan,
            evaluation=evaluate_plan(scenario, plan),
            solver="cp-sat",
            timed_out=status != cp_model.OPTIMAL,
        )
