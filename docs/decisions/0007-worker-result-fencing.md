# 0007. Persist recommendations under an expiring worker lease

Workers carry a typed job lease containing job, scenario, team, owner, and attempt.
Claims use PostgreSQL SKIP LOCKED. Completion, heartbeat, and failure check that
same lease and the server clock. Reusing an owner name never lets an old attempt
write. A crash at the final attempt becomes failed when the next worker polls.
Cancellation invalidates the lease immediately; retries use bounded backoff.

Recommendation insertion and job success commit in one transaction. An insertion
failure rolls back success. The solver is bounded to 10 seconds by default and
the worker lease defaults to 60 seconds. Larger workloads must renew the lease;
there is no automatic heartbeat thread.

The PostgreSQL worker adapter is exercised against a real database. The HTTP
reference API still keeps scenarios and decisions in memory, so its approvals
are not restart-durable. Do not infer a production database API deployment from
these worker tests. The persistent decisions table rejects UPDATE and DELETE.
