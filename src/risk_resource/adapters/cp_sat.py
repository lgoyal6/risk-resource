from __future__ import annotations

from ortools.sat.python import cp_model

from risk_resource.domain.checker import evaluate_plan
from risk_resource.domain.greedy import greedy_plan
from risk_resource.domain.models import Assignment, Plan, Scenario, SolveResult
from risk_resource.domain.objective import risk_value_cents, travel_cost_cents


class CpSatSolver:
    """CP-SAT model of the same hard constraints and objective as the checker.

    The checker stays the authority: callers re-check every returned plan.
    """

    def solve(
        self, scenario: Scenario, *, max_seconds: float = 10.0, should_stop=None
    ) -> SolveResult:
        model = cp_model.CpModel()
        orders, techs = scenario.work_orders, scenario.technicians
        assets = {a.id: a for a in scenario.assets}
        quantile = scenario.planning_quantile
        vars: dict[tuple[str, str, int], cp_model.IntVar] = {}
        for w in orders:
            asset = assets[w.asset_id]
            for t in techs:
                if w.skill not in t.skills or asset.site not in t.cleared_sites:
                    continue
                for day in range(
                    w.earliest_day, min(w.latest_day, scenario.horizon_days - 1) + 1
                ):
                    vars[(w.id, t.id, day)] = model.new_bool_var(
                        f"x_{w.id}_{t.id}_{day}"
                    )
        by_order: dict[str, list[tuple[int, cp_model.IntVar]]] = {
            w.id: [] for w in orders
        }
        for (wid, _, day), v in vars.items():
            by_order[wid].append((day, v))
        scheduled = {}
        day_of = {}
        for w in orders:
            choices = [v for _, v in by_order[w.id]]
            scheduled[w.id] = sum(choices)
            day_of[w.id] = sum(day * v for day, v in by_order[w.id])
            (
                model.add(sum(choices) == 1)
                if w.mandatory
                else model.add(sum(choices) <= 1)
            )
        # Precedence: a scheduled order needs every predecessor on an earlier day.
        for w in orders:
            if not w.after:
                continue
            if not by_order[w.id]:
                continue  # never schedulable, so precedence cannot bind
            active = model.new_bool_var(f"s_{w.id}")
            model.add(scheduled[w.id] == active)
            for predecessor in w.after:
                model.add(scheduled[predecessor] >= active)
                model.add(day_of[predecessor] + 1 <= day_of[w.id]).only_enforce_if(
                    active
                )
        for t in techs:
            for day in range(scenario.horizon_days):
                model.add(
                    sum(
                        vars[(w.id, t.id, day)]
                        * (w.p50_minutes if quantile == "p50" else w.p90_minutes)
                        for w in orders
                        if (w.id, t.id, day) in vars
                    )
                    <= t.available_minutes_per_day
                )
        # Drain capacity counts distinct offline assets per cluster and day.
        # A cluster absent from max_offline_per_day has limit 0, as in the checker.
        clusters = {a.cluster for a in scenario.assets}
        asset_of = {w.id: w.asset_id for w in orders}
        for cluster in clusters:
            limit = scenario.max_offline_per_day.get(cluster, 0)
            for day in range(scenario.horizon_days):
                offline = []
                for asset in scenario.assets:
                    if asset.cluster != cluster:
                        continue
                    relevant = [
                        v
                        for (wid, _, d), v in vars.items()
                        if d == day and asset_of[wid] == asset.id
                    ]
                    if not relevant:
                        continue
                    down = model.new_bool_var(f"down_{asset.id}_{day}")
                    for v in relevant:
                        model.add_implication(v, down)
                    offline.append(down)
                if offline:
                    model.add(sum(offline) <= limit)
        # Checker objective = risk - travel - overdue. Overdue is a constant total
        # minus the penalty avoided by finishing on or before the due day.
        overdue_total = sum(
            round(w.overdue_penalty_usd * 100 * scenario.overdue_weight) for w in orders
        )
        by_id = {w.id: w for w in orders}
        model.maximize(
            sum(
                v
                * (
                    risk_value_cents(scenario, by_id[wid], day)
                    - travel_cost_cents(scenario, tid, wid)
                    + (
                        round(
                            by_id[wid].overdue_penalty_usd
                            * 100
                            * scenario.overdue_weight
                        )
                        if day <= by_id[wid].due_day
                        else 0
                    )
                )
                for (wid, tid, day), v in vars.items()
            )
            - overdue_total
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
                proven_infeasible=status == cp_model.INFEASIBLE,
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
            objective_bound_cents=round(solver.best_objective_bound),
        )
