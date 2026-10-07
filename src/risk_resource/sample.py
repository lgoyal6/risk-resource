from .domain.models import Asset, Provenance, Scenario, Skill, Technician, WorkOrder


def demo_scenario() -> Scenario:
    assets = (
        Asset(id="gpu-01", site="sjc", cluster="train-a", downtime_cost_usd=12000),
        Asset(id="gpu-02", site="sjc", cluster="train-a", downtime_cost_usd=9000),
        Asset(id="gpu-03", site="pdx", cluster="train-b", downtime_cost_usd=7000),
    )
    techs = (
        Technician(
            id="tech-1",
            home_site="sjc",
            skills=frozenset({Skill.GPU, Skill.FIRMWARE}),
            available_minutes_per_day=240,
            cleared_sites=frozenset({"sjc"}),
        ),
        Technician(
            id="tech-2",
            home_site="pdx",
            skills=frozenset({Skill.NETWORK, Skill.GPU}),
            available_minutes_per_day=180,
            cleared_sites=frozenset({"pdx", "sjc"}),
        ),
    )
    orders = (
        WorkOrder(
            id="wo-01",
            asset_id="gpu-01",
            skill=Skill.GPU,
            earliest_day=0,
            latest_day=2,
            due_day=1,
            p50_minutes=60,
            p90_minutes=90,
            failure_probability_low=0.15,
            failure_probability_high=0.25,
            risk_reduction=0.8,
            risk_window_days=30,
            mandatory=True,
            overdue_penalty_usd=2000,
        ),
        WorkOrder(
            id="wo-02",
            asset_id="gpu-02",
            skill=Skill.FIRMWARE,
            earliest_day=0,
            latest_day=3,
            due_day=2,
            p50_minutes=45,
            p90_minutes=75,
            failure_probability_low=0.05,
            failure_probability_high=0.12,
            risk_reduction=0.7,
            risk_window_days=30,
            overdue_penalty_usd=800,
        ),
        WorkOrder(
            id="wo-03",
            asset_id="gpu-03",
            skill=Skill.GPU,
            earliest_day=1,
            latest_day=3,
            due_day=3,
            p50_minutes=90,
            p90_minutes=120,
            failure_probability_low=0.08,
            failure_probability_high=0.18,
            risk_reduction=0.65,
            risk_window_days=30,
            overdue_penalty_usd=500,
        ),
    )
    return Scenario(
        id="demo-week",
        name="Demo maintenance week",
        provenance=Provenance.DEMO,
        horizon_days=4,
        max_offline_per_day={"train-a": 1, "train-b": 1},
        assets=assets,
        technicians=techs,
        work_orders=orders,
    )
