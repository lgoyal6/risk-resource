# 0006. Bind API decisions to an authenticated team

The API now requires an `X-API-Key` that matches a configured SHA-256 digest. The digest maps
to a user, team, and role. Every scenario read and write is team-scoped, and decision actors are
taken from the authenticated principal rather than the request body. Decision writes require an
`Idempotency-Key`; a retry returns the original hash-chain entry instead of appending another one. Reusing a key for a changed payload or authenticated actor returns 409. A process-local lock serializes the key check and hash-chain append. These caches remain in memory and are not restart-durable.

Compose requires `RR_API_KEYS`; the API returns 503 for protected requests when keys are unconfigured. The health endpoint stays
unauthenticated for the container probe, while scenario data and approval history require a role.
