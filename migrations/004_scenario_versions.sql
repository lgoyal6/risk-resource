-- ADR 0008: immutable scenario versions, pinned solve jobs, digest-bound decisions.
ALTER TABLE scenarios ADD COLUMN IF NOT EXISTS current_version int NOT NULL DEFAULT 1;
CREATE TABLE IF NOT EXISTS scenario_versions(
    scenario_id text NOT NULL REFERENCES scenarios(id),
    version int NOT NULL CHECK (version >= 1),
    raw_json jsonb NOT NULL,
    content_sha256 text NOT NULL,
    created_by text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scenario_id, version)
);
INSERT INTO scenario_versions(scenario_id, version, raw_json, content_sha256)
    SELECT id, 1, raw_json, content_sha256 FROM scenarios ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION deny_version_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'scenario versions are immutable'; END $$;
DROP TRIGGER IF EXISTS scenario_versions_immutable ON scenario_versions;
CREATE TRIGGER scenario_versions_immutable BEFORE UPDATE OR DELETE ON scenario_versions FOR EACH ROW EXECUTE FUNCTION deny_version_mutation();
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS scenario_version int NOT NULL DEFAULT 1;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS diagnosis jsonb;
ALTER TABLE jobs DROP CONSTRAINT IF EXISTS jobs_status_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_status_check CHECK (status IN ('queued','running','succeeded','failed','cancelled','infeasible'));
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS scenario_version int NOT NULL DEFAULT 1;
ALTER TABLE recommendations ADD COLUMN IF NOT EXISTS solution_digest text;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS scenario_version int;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS solution_digest text;
ALTER TABLE decisions ADD COLUMN IF NOT EXISTS objective_delta_cents bigint;
