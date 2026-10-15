# Decisions

Short records of the choices worth defending. Each has the alternative that was rejected.

## 1. Postgres is the queue (no Redis, Celery or Kafka)

**Decision.** Due work is a `SELECT ... FOR UPDATE SKIP LOCKED` on the `monitors` table.

**Why.** The data and the queue are already in one transactional system, so "record the result and
schedule the next run" is one atomic commit. Throughput needs are tiny (tens of probes per second
at most).

**Rejected.** A broker adds a second system to run and a second place state can disagree. It would
make sense at a scale this project does not have.

## 2. A lease column instead of fencing tokens

**Decision.** Claiming sets `locked_until = now() + 2 x timeout + 15 s`. Expiry is the only
recovery mechanism.

**Why.** It is one column and one comparison, and it handles the case that matters: a worker that
dies. The lease is much longer than a probe can run (the probe is bounded by its own timeout), so a
live worker does not lose its lease mid-probe in practice.

**Cost.** In the worst case a monitor is probed twice and has two runs. That is harmless for a
monitor, and avoiding it would need fencing tokens and an idempotency key.

## 3. Never hold a transaction across the probe

**Decision.** Claim, commit, probe, then open a new transaction to record.

**Why.** Holding a transaction (and its row locks and a pooled connection) open while waiting on an
arbitrary remote server lets one slow target exhaust the pool.

## 4. Pin the connection to the validated IP

**Decision.** Resolve once, validate every address, then connect to that IP and send the original
`Host` header and SNI.

**Why.** Validating a hostname and then letting the HTTP client resolve it again leaves a window
for DNS rebinding: the first answer is public, the second is `169.254.169.254`.

## 5. Open an incident after N consecutive failures

**Decision.** Default 3 consecutive failures to open, first pass to resolve.

**Why.** One dropped packet should not be an incident. **Cost:** a real outage is noticed
`N - 1` probes late. Resolving on the first pass is the simplest rule that does not leave
incidents open forever; it can flap if a service alternates pass/fail, which is accepted.

## 6. Scheduler state lives on the `monitors` row

**Decision.** `next_run_at`, `locked_until`, `status` and the failure counter are columns of
`monitors`, not a separate state table.

**Why.** One table and one row lock per monitor. Config edits and worker updates touch disjoint
columns.

## 7. Server-side sessions, not JWTs

**Decision.** Opaque random tokens in an `HttpOnly` cookie, hashed in the database.

**Why.** Logout and expiry are real (delete the row). No signing keys to manage.

## 8. Faultline instead of mocking the network

**Decision.** Tests probe a real local server that can hang, reset, drip bytes or serve a bad
certificate.

**Why.** Failure classification depends on real socket behaviour, which mocks tend to get wrong.
