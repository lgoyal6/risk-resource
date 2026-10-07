ALTER TABLE jobs ADD COLUMN IF NOT EXISTS next_retry_at timestamptz;
CREATE INDEX IF NOT EXISTS jobs_claim_ready ON jobs(status,next_retry_at,lease_expires_at);
