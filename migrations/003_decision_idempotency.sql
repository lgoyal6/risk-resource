CREATE TABLE IF NOT EXISTS decision_idempotency(
    team text NOT NULL,
    scenario_id text NOT NULL REFERENCES scenarios(id),
    idempotency_key text NOT NULL,
    request_hash text NOT NULL,
    decision_hash text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(team, scenario_id, idempotency_key)
);
