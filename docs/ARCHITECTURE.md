# Architecture

## Components

| Piece | Job |
|---|---|
| `api` (FastAPI) | Auth, monitor CRUD, list runs and incidents, serves the built UI. Never probes. |
| `worker` | The only scheduler. Claims due monitors, probes, records results. Run as many as you like. |
| PostgreSQL | Stores everything, and is the job queue. |
| Faultline | A deliberately breakable API for the demo and for tests. Tests never touch the internet. |

## Data model

Five tables.

```
users 1──* sessions
users 1──* monitors 1──* check_runs
                    1──* incidents      (at most one with resolved_at IS NULL per monitor)
```

`monitors` holds both the user's configuration and the scheduler's bookkeeping:

| Column | Meaning |
|---|---|
| `next_run_at` | The monitor is due when this has passed. |
| `locked_until` | The lease. A worker that claimed the monitor owns it until this time. |
| `status` | `UNKNOWN`, `UP`, or `DOWN`. `DOWN` means an incident is open. |
| `consecutive_failures` | Current streak, compared against `failure_threshold`. |

`check_runs` stores one row per probe: outcome, failure class, HTTP status, the four phase
timings, the IP that was connected to, and a detail string.

## One probe, end to end

```
worker loop
  TX1  claim_due     UPDATE monitors SET locked_until = now() + lease
                     WHERE id IN (SELECT id ... WHERE next_run_at <= now()
                                  AND (locked_until IS NULL OR locked_until < now())
                                  FOR UPDATE SKIP LOCKED LIMIT n)
       ── commit ──  (no transaction is open from here)
       run_probe     resolve -> check every IP -> connect to the pinned IP -> judge response
  TX2  record_result SELECT ... FOR UPDATE the monitor row
                     INSERT check_run
                     state_machine.evaluate(...)  -> OPEN / RESOLVE / NONE
                     INSERT or UPDATE incident
                     UPDATE monitor: status, counter, next_run_at = now()+interval, locked_until = NULL
       ── commit ──
```

The two transactions are short and never wait on the network. A transaction held open across a
slow target would pin a connection and its row locks for as long as the target felt like taking.

### Failure cases

| What goes wrong | What happens |
|---|---|
| Worker killed mid-probe | Nothing is written. `locked_until` passes, another worker claims it, the monitor is probed again. |
| Database down | `claim_due` fails, the worker logs, sleeps and retries. Nothing is half-written. |
| Probe finishes after the monitor was deleted | `record_result` finds no row and returns `False`. |
| Two workers poll at once | `SKIP LOCKED` gives each a disjoint set of monitors. |

## Incident rule

`incidents/state_machine.py` is a pure function: `(status, consecutive_failures, passed,
failure_threshold) -> (status, consecutive_failures, action)`.

- A failed probe increments the counter. When it reaches `failure_threshold` and the monitor is
  not already `DOWN`, the action is `OPEN`.
- A passed probe resets the counter. If the monitor was `DOWN`, the action is `RESOLVE`.
- `opened_at` is backdated to the first failure of the streak.
- A partial unique index (`one_open_incident`) guarantees at most one open incident per monitor.

## SSRF guard

The service makes requests to URLs that users type in, so it must not be usable to reach internal
addresses (cloud metadata at `169.254.169.254`, `localhost`, private networks).

1. `validate_url`: scheme must be http/https, no credentials, port in the allow-list, no
   internal-looking hostnames, no numeric hosts like `2130706433`.
2. Resolve the name, then require **every** returned address to be public (`allows_ip`). IPv4
   addresses embedded in IPv6 are unwrapped before checking.
3. Connect to the validated IP, not the name, so a second DNS lookup cannot return a different
   answer (DNS rebinding). `Host` and TLS SNI still carry the hostname, so virtual hosting and
   certificate verification work.
4. Redirects are not followed.

The check runs on every probe, not only when the monitor is created, because DNS can change.

## Auth and ownership

Sessions are random 256-bit tokens in an `HttpOnly`, `SameSite=Lax` cookie; only the SHA-256 is
stored. Passwords are argon2id. Every query on user-owned data filters by `owner_id` and returns
404 (not 403) for someone else's resource so existence is not confirmed.
