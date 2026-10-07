"""Reproducible local benchmark for named demo workload."""
from time import perf_counter
from risk_resource.sample import demo_scenario
from risk_resource.adapters.cp_sat import CpSatSolver
from risk_resource.domain.greedy import greedy_plan
from risk_resource.domain.checker import evaluate_plan

def main():
    scenario = demo_scenario(); start = perf_counter(); baseline = evaluate_plan(scenario, greedy_plan(scenario)); baseline_ms = (perf_counter()-start)*1000
    start = perf_counter(); optimized = CpSatSolver().solve(scenario, max_seconds=10); optimized_ms = (perf_counter()-start)*1000
    print({"workload":"demo-week","seed":"bundled-fixed","orders":len(scenario.work_orders),"baseline_objective_cents":baseline.objective_cents,"optimized_objective_cents":optimized.evaluation.objective_cents,"baseline_ms":round(baseline_ms,2),"optimized_ms":round(optimized_ms,2)})
if __name__ == "__main__": main()
