# ADR 0001: Domain is preventive maintenance scheduling for a GPU server fleet

Status: accepted (2026-10-06)

## Context

The build prompt asks for one concrete resource-allocation domain, not a generic risk
dashboard. Candidates were cloud capacity allocation, engineering support prioritization,
and maintenance scheduling.

- Cloud capacity allocation overlaps almost entirely with GPU Broker, which already
  schedules GPU capacity. A second project in the same shape adds no new evidence.
- Support prioritization has a weak "scarce resource" story: queues are usually
  ordered, not packed, and the uncertainty (ticket effort) is hard to ground.
- Maintenance scheduling has a real knapsack-with-side-constraints core, a natural
  uncertainty (which machines will fail, how long work takes), and a human approval step
  that exists in practice (an ops lead signs off the weekly maintenance plan).

## Decision

Model one planning week of **preventive maintenance for a GPU server fleet** spread
across a few datacenter sites.

**Scarce resource:** field technicians' minutes per day. Secondary scarce resource:
how many machines a cluster can lose at once (drain budget) before training jobs on it
are starved.

**Requests:** maintenance work orders (replace a degrading HBM-flagged GPU, reseat an
InfiniBand cable with rising symbol errors, swap a fan tray, update BMC firmware). Each
targets one asset.

**Uncertainty:**

1. Each work order carries a failure probability for its asset over a risk window
   (default 30 days) if the work is not done, with a low/high range. Telemetry-derived
   probabilities are estimates, so the range is first-class input, not a footnote.
2. Work duration is uncertain. Each order has a P50 and P90 estimate in minutes.

## Objective

Maximise expected avoided downtime cost, in cents, minus soft-constraint penalties:

```
value(w, d)  = failure_prob(w) * downtime_cost(asset) * risk_reduction(w)
               * (risk_window(w) - d) / risk_window(w)
objective    = sum over scheduled (w, t, d) of  W_risk * value(w, d)
             - W_travel  * travel_cost      for each assignment where t works away from home site
             - W_overdue * overdue_penalty  for each order with a due day that is unscheduled
                                            or scheduled after the due day
```

`(risk_window - d) / risk_window` encodes the assumption that failure hazard is uniform
over the risk window, so doing the work later in the week forfeits the slice of risk
that elapsed before the work. This is what makes the choice of day matter, and it is
stated, not hidden.

All objective terms are computed by one function (`domain.objective.coefficients`) and
rounded to integer cents per term. The greedy baseline, the CP-SAT model, and the plan
evaluator all read the same table, so their objective values are directly comparable.

## Hard constraints

1. A work order is scheduled at most once (one technician, one day).
2. The technician has the required skill and is cleared for the asset's site.
3. The day is inside the order's window `[earliest_day, latest_day]`.
4. Per technician per day, the sum of planned minutes does not exceed available minutes.
   Planned minutes are the P90 estimate by default (`planning_quantile`), P50 if the
   scenario says so.
5. Per cluster per day, the number of distinct assets taken offline does not exceed the
   cluster's `max_offline_per_day`.
6. Precedence: if order B lists A in `after`, B may only be scheduled if A is scheduled
   on a strictly earlier day.
7. Mandatory orders (safety or compliance) must be scheduled.

## Soft constraints

1. Travel: a technician working at a site other than their home site costs
   `travel_cost_usd` per assignment.
2. Overdue: an order with `due_day` that is not done by that day costs its
   `overdue_penalty_usd`.

## Unacceptable outcomes

A plan with any of these is never presented as a recommendation, and an override that
introduces one is refused:

- a mandatory work order left unscheduled;
- a cluster drained past its per-day limit;
- a technician booked beyond available minutes at the planning quantile;
- a recommendation that cannot be reproduced bit-for-bit from its exported bundle.

The greedy baseline is allowed to produce an unacceptable plan; it is a reference point,
and the UI and report label it as unacceptable when it does.

## Consequences

- Data is synthetic. Workload generators are seeded and named; nothing claims to be fleet
  telemetry. See ADR 0004.
- The model ignores intra-day sequencing (which hour), parts inventory, and failures that
  occur during the week changing the plan. These are listed in README limitations.
